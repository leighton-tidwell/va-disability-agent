"""Closing Output Generator.

For each Claimed Condition at the end of a session, produce a single
self-contained Markdown bundle (Lay Statement + supporting reports +
missing Evidence + C&P Exam Prep + va.gov filing pointers) the veteran can
copy from directly.

Read-only with respect to the graph: this module never writes back. It
queries the user-side subgraph for the data already persisted by slices
#7-#10 and emits Markdown to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..graph.driver import GraphDriver

# The va.gov filing template lives in data/ as plain Markdown with `{{name}}`
# placeholders. We load it once per call.
_TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "data" / "va_gov_filing_template.md"


@dataclass
class _ConditionBundle:
    dc_code: str
    dc_title: str
    body_system: str | None
    best_percent: int | None
    n_criteria: int | None
    lay_statement: dict[str, Any] | None
    exam_prep: dict[str, Any] | None
    symptom_reports: list[dict[str, Any]] = field(default_factory=list)
    measurement_reports: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    weaknesses: list[dict[str, Any]] = field(default_factory=list)


def generate_session_output(
    driver: GraphDriver, user_id: str, claim_id: str, output_dir: Path
) -> Path:
    """Render the Closing Output bundle for one Claim.

    Layout written under ``output_dir / claim_id``:

    - ``README.md`` — veteran profile + index of Claimed Conditions
    - ``dc-<code>.md`` per Claimed Condition
    - ``va-gov-filing-steps.md`` — filing walkthrough rendered from the
      template in ``data/``

    Returns the per-session directory path.
    """
    session_dir = Path(output_dir) / claim_id
    session_dir.mkdir(parents=True, exist_ok=True)

    profile = _fetch_profile(driver, user_id)
    bundles = _fetch_bundles(driver, user_id, claim_id)

    # Per-condition files.
    for bundle in bundles:
        md = _render_condition_md(bundle)
        (session_dir / f"dc-{bundle.dc_code}.md").write_text(md)

    # va.gov filing steps.
    filing_md = _render_filing_steps(claim_id, bundles)
    (session_dir / "va-gov-filing-steps.md").write_text(filing_md)

    # Top-level index.
    readme_md = _render_readme(profile, bundles, claim_id)
    (session_dir / "README.md").write_text(readme_md)

    return session_dir


# --- Graph reads -----------------------------------------------------------


def _fetch_profile(driver: GraphDriver, user_id: str) -> dict[str, Any]:
    rows = driver.user_read(
        user_id,
        """
        MATCH (v:User:Veteran {user_id: $user_id})
        OPTIONAL MATCH (v)-[:DEPLOYED_TO]->(dep:User:Deployment)
        OPTIONAL MATCH (v)-[:HOLDS_JOBCODE]->(jc:CFR:JobCode)
        RETURN v.branch AS branch,
               v.discharge_characterization AS discharge,
               collect(DISTINCT dep.name) AS deployments,
               collect(DISTINCT jc.code)[0] AS job_code,
               collect(DISTINCT jc.branch)[0] AS job_code_branch
        """,
        params={},
    )
    if not rows:
        return {}
    return dict(rows[0])


def _fetch_bundles(
    driver: GraphDriver, user_id: str, claim_id: str
) -> list[_ConditionBundle]:
    """One query per condition; keeps Cypher legible."""
    cc_rows = driver.user_read(
        user_id,
        """
        MATCH (cl:User:Claim {id: $claim_id, user_id: $user_id})
              -[:CLAIMS]->(cc:User:ClaimedCondition)
              -[:MAPS_TO]->(dc:CFR:DiagnosticCode)
        RETURN dc.code AS code, dc.title AS title, dc.body_system AS body_system
        ORDER BY dc.code
        """,
        params={"claim_id": claim_id},
    )

    bundles: list[_ConditionBundle] = []
    for row in cc_rows:
        code = row["code"]

        lay = _fetch_latest_lay_statement(driver, user_id, code)
        prep = _fetch_latest_exam_prep(driver, user_id, code)

        body_parts = _condition_body_parts(driver, code)
        symptoms = _fetch_symptom_reports(driver, user_id, body_parts)
        measurements = _fetch_measurement_reports(driver, user_id, body_parts)
        missing = _fetch_missing_evidence(driver, user_id, claim_id, code)
        weaknesses = _fetch_weaknesses(driver, user_id, claim_id, code)
        best_percent, n_criteria = _fetch_best_match(driver, user_id, code)

        bundles.append(
            _ConditionBundle(
                dc_code=code,
                dc_title=row["title"],
                body_system=row["body_system"],
                best_percent=best_percent,
                n_criteria=n_criteria,
                lay_statement=lay,
                exam_prep=prep,
                symptom_reports=symptoms,
                measurement_reports=measurements,
                missing_evidence=missing,
                weaknesses=weaknesses,
            )
        )
    return bundles


def _fetch_latest_lay_statement(
    driver: GraphDriver, user_id: str, dc_code: str
) -> dict[str, Any] | None:
    rows = driver.user_read(
        user_id,
        """
        MATCH (ls:User:LayStatement {user_id: $user_id, dc_code: $code})
        RETURN ls.text AS text, ls.factuality_ok AS factuality_ok,
               ls.version AS version, ls.generated_at AS generated_at
        ORDER BY ls.version DESC LIMIT 1
        """,
        params={"code": dc_code},
    )
    return dict(rows[0]) if rows else None


def _fetch_latest_exam_prep(
    driver: GraphDriver, user_id: str, dc_code: str
) -> dict[str, Any] | None:
    rows = driver.user_read(
        user_id,
        """
        MATCH (ep:User:ExamPrep {user_id: $user_id, dc_code: $code})
        RETURN ep.will_measure AS will_measure,
               ep.describe_to_examiner AS describe_to_examiner,
               ep.records_to_bring AS records_to_bring,
               ep.notes AS notes, ep.version AS version
        ORDER BY ep.version DESC LIMIT 1
        """,
        params={"code": dc_code},
    )
    return dict(rows[0]) if rows else None


def _condition_body_parts(driver: GraphDriver, dc_code: str) -> list[str]:
    rows = driver.cfr_read(
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})
        OPTIONAL MATCH (dc)-[:HAS_RATING]->()-[:REQUIRES]->(c:Criterion)
                       -[:HAS_MEASUREMENT]->(m:Measurement)
        OPTIONAL MATCH (dc)-[:RATES]->(a:CFR:Anatomy)
        WITH collect(DISTINCT m.body_part) + collect(DISTINCT a.name) AS parts
        RETURN [p IN parts WHERE p IS NOT NULL] AS parts
        """,
        params={"code": dc_code},
    )
    if not rows:
        return []
    return sorted(set(rows[0]["parts"] or []))


def _fetch_symptom_reports(
    driver: GraphDriver, user_id: str, body_parts: list[str]
) -> list[dict[str, Any]]:
    if not body_parts:
        return []
    rows = driver.user_read(
        user_id,
        """
        MATCH (v:User:Veteran {user_id: $user_id})-[:REPORTED]->(sr:User:SymptomReport)
        WHERE sr.body_part IN $parts
        RETURN sr.body_part AS body_part, sr.text AS text,
               sr.typical_severity AS baseline,
               sr.flareup_severity AS flare_severity,
               sr.flareup_frequency AS frequency,
               sr.flareup_duration AS duration,
               sr.functional_loss AS functional_loss
        ORDER BY body_part
        """,
        params={"parts": body_parts},
    )
    return [dict(r) for r in rows]


def _fetch_measurement_reports(
    driver: GraphDriver, user_id: str, body_parts: list[str]
) -> list[dict[str, Any]]:
    if not body_parts:
        return []
    rows = driver.user_read(
        user_id,
        """
        MATCH (v:User:Veteran {user_id: $user_id})-[:HAS_MEASUREMENT]->(mr:User:MeasurementReport)
        WHERE mr.body_part IN $parts
        RETURN mr.name AS name, mr.body_part AS body_part,
               mr.value AS value, mr.unit AS unit, mr.source AS source
        ORDER BY body_part, name
        """,
        params={"parts": body_parts},
    )
    return [dict(r) for r in rows]


def _fetch_missing_evidence(
    driver: GraphDriver, user_id: str, claim_id: str, dc_code: str
) -> list[str]:
    # Mirror the slice #8 reviewer: surface the gatherable types that aren't
    # yet attached. We compute it fresh rather than depending on a persisted
    # report node — the reviewer's output isn't always persisted.
    from ..review.claim_reviewer import GATHERABLE_EVIDENCE_TYPES

    rows = driver.user_read(
        user_id,
        """
        MATCH (cl:User:Claim {id: $claim_id, user_id: $user_id})
              -[:CLAIMS]->(cc:User:ClaimedCondition {dc_code: $code, user_id: $user_id})
        OPTIONAL MATCH (cc)-[:SUPPORTED_BY]->(e:User:Evidence)
        RETURN collect(DISTINCT e.type) AS attached
        """,
        params={"claim_id": claim_id, "code": dc_code},
    )
    attached = set(rows[0]["attached"] or []) if rows else set()
    return [t for t in GATHERABLE_EVIDENCE_TYPES if t not in attached]


def _fetch_weaknesses(
    driver: GraphDriver, user_id: str, claim_id: str, dc_code: str
) -> list[dict[str, Any]]:
    rows = driver.user_read(
        user_id,
        """
        MATCH (cl:User:Claim {id: $claim_id, user_id: $user_id})-[:HAS_WEAKNESS]->(w:User:Weakness)
        WHERE $code IN w.dc_codes
        RETURN w.kind AS kind, w.anatomy AS anatomy,
               w.explanation AS explanation, w.citation AS citation,
               w.dc_codes AS dc_codes
        """,
        params={"claim_id": claim_id, "code": dc_code},
    )
    return [dict(r) for r in rows]


def _fetch_best_match(
    driver: GraphDriver, user_id: str, dc_code: str
) -> tuple[int | None, int | None]:
    """Best-supported Rating Percentage + matched-criteria count.

    Approximates the matcher logic without re-importing it: counts how many of
    the DC's criteria the veteran's MeasurementReports satisfy. If none
    match, returns ``(None, 0)``.
    """
    rows = driver.user_read(
        user_id,
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)
              -[:REQUIRES]->(c:Criterion)-[:HAS_MEASUREMENT]->(m:Measurement)
        OPTIONAL MATCH (v:User:Veteran {user_id: $user_id})-[:HAS_MEASUREMENT]->(mr:User:MeasurementReport)
        WHERE mr.name = m.name AND mr.body_part = m.body_part AND mr.unit = m.unit
          AND (
              (m.operator = '<=' AND mr.value <= m.value) OR
              (m.operator = '>=' AND mr.value >= m.value) OR
              (m.operator = '<'  AND mr.value <  m.value) OR
              (m.operator = '>'  AND mr.value >  m.value) OR
              (m.operator = '='  AND mr.value =  m.value)
          )
        WITH rl.percent AS percent, count(DISTINCT mr) AS hits
        WHERE hits > 0
        RETURN percent, hits ORDER BY percent DESC LIMIT 1
        """,
        params={"code": dc_code},
    )
    if not rows:
        return None, 0
    return int(rows[0]["percent"]), int(rows[0]["hits"])


# --- Rendering -------------------------------------------------------------


def _render_condition_md(b: _ConditionBundle) -> str:
    lines: list[str] = []
    lines.append(f"# DC {b.dc_code} — {b.dc_title}")
    lines.append("")
    best = f"{b.best_percent}%" if b.best_percent is not None else "not yet matched"
    n = b.n_criteria if b.n_criteria is not None else 0
    lines.append(f"**Best-supported Rating Percentage:** {best} (matched {n} criteria)")
    lines.append(f"**Body System:** {b.body_system or 'unspecified'}")
    lines.append("")

    # Lay Statement
    lines.append("## Lay Statement (paste this into the va.gov claim description)")
    lines.append("")
    if b.lay_statement:
        for para in b.lay_statement["text"].split("\n"):
            lines.append(f"> {para}")
        lines.append("")
        status = "passed" if b.lay_statement.get("factuality_ok") else "FAILED — review before filing"
        lines.append(f"Factuality check: {status}")
        lines.append(
            f"(Version {b.lay_statement.get('version')}, "
            f"drafted {b.lay_statement.get('generated_at')})"
        )
    else:
        lines.append("> _No Lay Statement has been drafted for this Claimed Condition yet._")
    lines.append("")

    # Supporting reports
    lines.append("## What supports this Claimed Condition")
    lines.append("")
    lines.append("### Symptom reports")
    lines.append("")
    if not b.symptom_reports:
        lines.append("_None on file for this Diagnostic Code's anatomy._")
    for sr in b.symptom_reports:
        bullet = f"- **{sr['body_part']}**: {sr['text']}"
        lines.append(bullet)
        meta_bits = []
        if sr.get("baseline"):
            meta_bits.append(f"baseline={sr['baseline']}")
        if sr.get("flare_severity"):
            meta_bits.append(f"flare-up={sr['flare_severity']}")
        if sr.get("frequency"):
            meta_bits.append(f"({sr['frequency']}")
            if sr.get("duration"):
                meta_bits[-1] += f", lasting {sr['duration']}"
            meta_bits[-1] += ")"
        elif sr.get("duration"):
            meta_bits.append(f"(lasting {sr['duration']})")
        if meta_bits:
            lines.append(f"  {' '.join(meta_bits)}")
        if sr.get("functional_loss"):
            lines.append(f"  Functional loss: {', '.join(sr['functional_loss'])}")
    lines.append("")

    lines.append("### Measurements")
    lines.append("")
    if not b.measurement_reports:
        lines.append("_None on file. The C&P examiner will measure these during the exam._")
    for mr in b.measurement_reports:
        src = mr.get("source") or "user-stated"
        lines.append(
            f"- {mr['name']} ({mr['body_part']}) = {mr['value']} {mr['unit']}  *(source: {src})*"
        )
    lines.append("")

    # Evidence to gather
    lines.append("## Evidence to gather")
    lines.append("")
    if b.missing_evidence:
        for t in b.missing_evidence:
            lines.append(f"- [ ] {t}")
        lines.append("")
        lines.append("*(Lay Statement is the one above — already drafted.)*")
    else:
        lines.append("All Evidence types are attached to this Claimed Condition.")
    lines.append("")

    # Exam Prep
    lines.append("## C&P Exam Preparation")
    lines.append("")
    if b.exam_prep:
        will = b.exam_prep.get("will_measure") or []
        if will:
            lines.append("The examiner will measure:")
            for w in will:
                lines.append(f"- {w}")
            lines.append("")
        describe = b.exam_prep.get("describe_to_examiner") or []
        if describe:
            lines.append("When describing your condition, remember:")
            for d in describe:
                lines.append(f"- {d}")
            lines.append("")
        records = b.exam_prep.get("records_to_bring") or []
        if records:
            lines.append("Records to bring:")
            for r in records:
                lines.append(f"- {r}")
            lines.append("")
        notes = b.exam_prep.get("notes") or []
        if notes:
            lines.append("Notes:")
            for n_ in notes:
                lines.append(f"- {n_}")
            lines.append("")
    else:
        lines.append("_No Exam Prep has been generated for this Claimed Condition yet._")
        lines.append("")

    # Weaknesses
    if b.weaknesses:
        lines.append("## Weaknesses to address before filing")
        lines.append("")
        for w in b.weaknesses:
            lines.append(
                f"- {w.get('explanation', '')}  *(citation: {w.get('citation', 'n/a')})*"
            )
        lines.append("")

    # Filing pointer
    lines.append("## Filing this on va.gov")
    lines.append("")
    lines.append(
        "See `va-gov-filing-steps.md` in this folder for the step-by-step process. "
        "The key fields:"
    )
    lines.append(f"- Condition name: **{b.dc_title}** (or paste the DC code \"{b.dc_code}\")")
    lines.append("- Description field: paste the Lay Statement above")
    lines.append("")

    return "\n".join(lines)


def _render_readme(
    profile: dict[str, Any], bundles: list[_ConditionBundle], claim_id: str
) -> str:
    lines: list[str] = []
    lines.append(f"# Claim {claim_id} — Closing Output")
    lines.append("")
    lines.append(
        f"Generated {datetime.now().isoformat(timespec='seconds')}. "
        "Each Claimed Condition has its own file below — every file is "
        "self-contained so you can copy from it directly into va.gov."
    )
    lines.append("")

    lines.append("## Veteran profile")
    lines.append("")
    lines.append(f"- **Branch:** {profile.get('branch') or 'unspecified'}")
    lines.append(
        f"- **Discharge characterization:** {profile.get('discharge') or 'unspecified'}"
    )
    deployments = profile.get("deployments") or []
    if deployments:
        lines.append(f"- **Deployments:** {', '.join(deployments)}")
    else:
        lines.append("- **Deployments:** none recorded")
    jc = profile.get("job_code")
    if jc:
        lines.append(f"- **Job Code:** {jc} ({profile.get('job_code_branch') or 'unspecified'})")
    else:
        lines.append("- **Job Code:** unspecified")
    lines.append("")

    lines.append("## Claimed Conditions")
    lines.append("")
    if not bundles:
        lines.append("_No Claimed Conditions found for this Claim._")
    for b in bundles:
        best = f"{b.best_percent}%" if b.best_percent is not None else "—"
        lines.append(f"- [DC {b.dc_code} — {b.dc_title}](./dc-{b.dc_code}.md) ({best})")
    lines.append("")

    lines.append("## Filing")
    lines.append("")
    lines.append(
        "Walk through [`va-gov-filing-steps.md`](./va-gov-filing-steps.md) "
        "after reviewing each per-condition file. Bring the per-condition "
        "files to your C&P exams."
    )
    lines.append("")
    return "\n".join(lines)


def _render_filing_steps(claim_id: str, bundles: list[_ConditionBundle]) -> str:
    """Render the va.gov filing template with placeholder substitution."""
    template = _TEMPLATE_PATH.read_text()
    placeholders = _filing_placeholders(claim_id, bundles)
    return render_template(template, placeholders)


def _filing_placeholders(
    claim_id: str, bundles: list[_ConditionBundle]
) -> dict[str, str]:
    """Build the substitution dict for the va.gov filing template."""
    condition_list_lines = [
        f"- **{b.dc_title}** (DC {b.dc_code})" for b in bundles
    ] or ["- _none_"]

    # Pick the first available Lay Statement excerpt as an example.
    example_excerpt = "[your drafted Lay Statement]"
    example_title = bundles[0].dc_title if bundles else "[condition name]"
    for b in bundles:
        if b.lay_statement and b.lay_statement.get("text"):
            text = b.lay_statement["text"].strip()
            # First sentence-ish, capped at ~240 chars.
            first = text.split(". ")[0].strip()
            if len(first) > 240:
                first = first[:237] + "..."
            example_excerpt = first.rstrip(".") + "."
            example_title = b.dc_title
            break

    return {
        "claim_id": claim_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_conditions": str(len(bundles)),
        "condition_list": "\n".join(condition_list_lines),
        "condition_title_example": example_title,
        "lay_statement_excerpt": example_excerpt,
    }


def render_template(template: str, values: dict[str, str]) -> str:
    """Substitute ``{{name}}`` placeholders in ``template`` from ``values``.

    Unknown placeholders are left in place (rendered literally) — this keeps
    template authoring forgiving and makes missing data visible in the output.
    """
    # Convert {{name}} to Template's $name syntax, but only for names that
    # are valid identifiers we have values for; leave the rest untouched.
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out
