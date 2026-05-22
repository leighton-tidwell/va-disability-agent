"""Lay Statement Drafter.

For one (user_id, dc_code) pair, gather the veteran's confirmed reports and
the Diagnostic Code's Criteria, prompt the LLM to draft a CFR-vocabulary Lay
Statement that uses *only* the confirmed reports, then run the Cypher-backed
factuality check. On failure, retry once with the failures called out to the
LLM. If it still fails, raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ..graph.driver import GraphDriver
from ..graph.tools import get_measurement_reports, get_symptom_reports
from ..retrieval.factuality import FactualityResult, check_lay_statement


class _DraftSchema(BaseModel):
    lay_statement: str = Field(
        ...,
        description=(
            "A one-paragraph Lay Statement in CFR vocabulary, written in the "
            "veteran's first-person voice, drawing ONLY from the supplied "
            "reports. No invented symptoms, severities, or measurements."
        ),
    )


@dataclass
class LayStatementDraft:
    user_id: str
    dc_code: str
    text: str
    factuality: FactualityResult
    attempts: int


class DrafterLLM(Protocol):
    def draft(self, *, system: str, user: str) -> str: ...


class OpenAIDrafter:
    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.0) -> None:
        self._llm = ChatOpenAI(model=model, temperature=temperature).with_structured_output(
            _DraftSchema
        )

    def draft(self, *, system: str, user: str) -> str:
        result = self._llm.invoke([("system", system), ("user", user)])
        return result.lay_statement  # type: ignore[union-attr]


SYSTEM_PROMPT = """\
You are drafting a Lay Statement for a US veteran's VA disability claim.

You will be given two things:

1. The veteran's confirmed reports (SymptomReports + MeasurementReports).
2. The Diagnostic Code's title and the rating Criteria from 38 CFR Part 4.

Your output is ONE paragraph in the veteran's first-person voice
("I have …", "My …") that:

- Uses CFR vocabulary (e.g. "flexion limited to N degrees", "range of motion",
  "ankylosis", "frequent locking") when the report supports it.
- Includes the veteran's Baseline severity AND Flare-up severity / frequency
  / duration when present (the Worst-Day Rule, §4.40/§4.45).
- Names specific Functional Losses the veteran has reported.
- Does NOT introduce any symptom, severity, measurement, or body part NOT
  present in the reports.
- Does NOT round or change measurement values from the reports.
- Does NOT cite a specific DC number; the rater handles that.

If the reports don't include a Flare-up or Functional Loss, omit those — do
not invent them.
"""

REVISION_INSTRUCTIONS = """\
Your previous draft contained content not supported by the veteran's reports.
Specifically, these values/units were not present in any MeasurementReport:
{fabricated}

Rewrite the Lay Statement using ONLY values, units, and body parts that
appear in the veteran's reports. Do not include values that aren't in the
reports, even if rounding would put them close.
"""


def _gather_inputs(driver: GraphDriver, user_id: str, dc_code: str) -> tuple[list[dict], list[dict], list[dict]]:
    symptom_reports = get_symptom_reports(driver, user_id)
    measurement_reports = get_measurement_reports(driver, user_id)
    criteria = driver.cfr_read(
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)
              -[:REQUIRES]->(c:Criterion)
        OPTIONAL MATCH (c)-[:HAS_MEASUREMENT]->(m:Measurement)
        WITH rl.percent AS percent, c.text AS text,
             collect(DISTINCT m{.name, .body_part, .operator, .value, .unit}) AS measurements
        RETURN percent, text, measurements
        ORDER BY percent
        """,
        params={"code": dc_code},
    )
    return symptom_reports, measurement_reports, criteria


def _format_inputs(
    *,
    dc_code: str,
    dc_title: str,
    symptom_reports: list[dict],
    measurement_reports: list[dict],
    criteria: list[dict],
) -> str:
    lines = [
        f"DIAGNOSTIC CODE: {dc_code} — {dc_title}",
        "",
        "CRITERIA (CFR rating tiers and their thresholds):",
    ]
    for c in criteria:
        ms = c.get("measurements") or []
        m_strs = []
        for m in ms:
            if m and m.get("name"):
                m_strs.append(
                    f"{m['name']} {m['operator']} {m['value']} {m['unit']}"
                )
        m_suffix = f"  [{'; '.join(m_strs)}]" if m_strs else ""
        lines.append(f"  {c['percent']:>3}%  {c['text']}{m_suffix}")

    lines += ["", "VETERAN'S CONFIRMED SYMPTOM REPORTS:"]
    if not symptom_reports:
        lines.append("  (none)")
    for sr in symptom_reports:
        bits = [f"- {sr['body_part']}: {sr['text']}"]
        if sr.get("typical_severity"):
            bits.append(f"baseline={sr['typical_severity']}")
        if sr.get("flareup_severity"):
            bits.append(f"flare-up={sr['flareup_severity']}")
        if sr.get("flareup_frequency"):
            bits.append(f"frequency={sr['flareup_frequency']}")
        if sr.get("flareup_duration"):
            bits.append(f"duration={sr['flareup_duration']}")
        if sr.get("functional_loss"):
            bits.append("functional_loss=" + ", ".join(sr["functional_loss"]))
        lines.append("  " + "  ".join(bits))

    lines += ["", "VETERAN'S CONFIRMED MEASUREMENTS:"]
    if not measurement_reports:
        lines.append("  (none)")
    for mr in measurement_reports:
        lines.append(f"  - {mr['name']} ({mr['body_part']}) = {mr['value']} {mr['unit']}")

    lines += ["", "Now write the Lay Statement."]
    return "\n".join(lines)


def draft_lay_statement(
    driver: GraphDriver,
    user_id: str,
    dc_code: str,
    *,
    drafter: DrafterLLM | None = None,
    max_attempts: int = 2,
) -> LayStatementDraft:
    drafter = drafter or OpenAIDrafter()

    symptom_reports, measurement_reports, criteria = _gather_inputs(driver, user_id, dc_code)

    dc_meta = driver.cfr_read(
        "MATCH (dc:CFR:DiagnosticCode {code: $code}) RETURN dc.title AS title",
        params={"code": dc_code},
    )
    if not dc_meta:
        raise ValueError(f"DC {dc_code} not in graph")
    dc_title = dc_meta[0]["title"]

    user_msg = _format_inputs(
        dc_code=dc_code,
        dc_title=dc_title,
        symptom_reports=symptom_reports,
        measurement_reports=measurement_reports,
        criteria=criteria,
    )

    system = SYSTEM_PROMPT
    factuality: FactualityResult | None = None
    text = ""
    for attempt in range(1, max_attempts + 1):
        text = drafter.draft(system=system, user=user_msg)
        factuality = check_lay_statement(driver, user_id, text)
        if factuality.ok:
            return LayStatementDraft(
                user_id=user_id,
                dc_code=dc_code,
                text=text,
                factuality=factuality,
                attempts=attempt,
            )
        # Retry with revision instructions.
        fabricated_brief = "\n".join(
            f"  - {f['value']} {f['unit']}" for f in factuality.fabricated_facts
        )
        system = SYSTEM_PROMPT + "\n\n" + REVISION_INSTRUCTIONS.format(fabricated=fabricated_brief)

    assert factuality is not None
    return LayStatementDraft(
        user_id=user_id,
        dc_code=dc_code,
        text=text,
        factuality=factuality,
        attempts=max_attempts,
    )
