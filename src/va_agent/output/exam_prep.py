"""Exam Prep Generator — per-Claimed-Condition C&P Exam Preparation.

Deterministic by construction: given a (user_id, dc_code), pulls the DC's
Criteria + Measurements + the veteran's confirmed reports and assembles a
guidance bundle. No LLM is involved — the agent doesn't get to invent what
the examiner will measure.

Persists as ``:User:ExamPrep`` nodes with a version property so edits
(slice #9's Review/Edit) can supersede prior versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from ..graph.driver import GraphDriver


@dataclass
class ExamPrep:
    user_id: str
    dc_code: str
    dc_title: str
    will_measure: list[dict] = field(default_factory=list)
    describe_to_examiner: list[str] = field(default_factory=list)
    records_to_bring: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    version: int = 1


def generate_exam_prep(driver: GraphDriver, user_id: str, dc_code: str) -> ExamPrep:
    """Build an ExamPrep for one Claimed Condition. Reads law-side + user-side
    graph state; returns a populated dataclass. Does NOT persist; call
    ``persist_exam_prep`` to write a versioned ``:User:ExamPrep`` node."""

    dc_rows = driver.cfr_read(
        "MATCH (dc:CFR:DiagnosticCode {code: $code}) RETURN dc.title AS title",
        params={"code": dc_code},
    )
    if not dc_rows:
        raise ValueError(f"DC {dc_code} not in graph")
    dc_title = dc_rows[0]["title"]

    # What the examiner will measure: pull every distinct Measurement attached
    # to any Criterion of this DC.
    measurement_rows = driver.cfr_read(
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)
              -[:REQUIRES]->(c:Criterion)-[:HAS_MEASUREMENT]->(m:Measurement)
        RETURN DISTINCT m.name AS name, m.body_part AS body_part, m.unit AS unit
        ORDER BY body_part, name
        """,
        params={"code": dc_code},
    )
    will_measure = [dict(r) for r in measurement_rows]

    # The veteran's confirmed reports for this anatomy.
    body_parts = sorted({m["body_part"] for m in will_measure if m.get("body_part")})
    symptom_reports = []
    measurement_reports = []
    if body_parts:
        symptom_reports = driver.user_read(
            user_id,
            """
            MATCH (v:User:Veteran {user_id: $user_id})-[:REPORTED]->(sr:User:SymptomReport)
            WHERE sr.body_part IN $parts
            RETURN sr.body_part AS body_part, sr.text AS text,
                   sr.typical_severity AS baseline, sr.flareup_severity AS flare,
                   sr.flareup_frequency AS frequency, sr.flareup_duration AS duration,
                   sr.functional_loss AS functional_loss
            """,
            params={"parts": body_parts},
        )
        measurement_reports = driver.user_read(
            user_id,
            """
            MATCH (v:User:Veteran {user_id: $user_id})-[:HAS_MEASUREMENT]->(mr:User:MeasurementReport)
            WHERE mr.body_part IN $parts
            RETURN mr.name AS name, mr.body_part AS body_part, mr.value AS value, mr.unit AS unit
            """,
            params={"parts": body_parts},
        )

    # Build the "describe to examiner" list. Anchored on the Worst-Day Rule:
    # remind the veteran to describe Flare-ups and Functional Loss, not just
    # their Baseline presentation.
    describe: list[str] = []
    for sr in symptom_reports:
        bp = sr["body_part"]
        if sr.get("flare"):
            describe.append(
                f"For your {bp}: describe your worst-day severity "
                f"({sr['flare']}) — the C&P captures your normal presentation by default. "
                f"Mention the flare frequency ({sr.get('frequency') or 'unspecified'}) and "
                f"duration ({sr.get('duration') or 'unspecified'})."
            )
        if sr.get("functional_loss"):
            describe.append(
                f"For your {bp}: name specific Functional Losses the examiner should know "
                f"about — {', '.join(sr['functional_loss'])}."
            )
    if not describe:
        describe.append(
            "No reports recorded for this Diagnostic Code's anatomy yet. The examiner "
            "will rely entirely on what they observe — describe your worst-day "
            "presentation in your own words."
        )

    # Records to bring: standard set, slightly tailored by what's already on file.
    records_to_bring = [
        "Any Service Treatment Record (STR) excerpt that references this condition",
        "Recent Private Medical Records (any visit in the last 12 months)",
        "Buddy Statements from anyone who has witnessed your symptoms",
        "Your own Lay Statement (we'll draft one — bring the final version)",
    ]
    if measurement_reports:
        records_to_bring.insert(
            0,
            "Any measurement results you mentioned to me: "
            + ", ".join(
                f"{mr['name']} ({mr['body_part']}) = {mr['value']} {mr['unit']}"
                for mr in measurement_reports
            ),
        )

    notes: list[str] = []
    if not measurement_reports:
        notes.append(
            "No measurements on file for this DC's anatomy — the examiner will measure "
            "during the exam. Ask them to test on a flare day if your symptoms vary."
        )

    return ExamPrep(
        user_id=user_id,
        dc_code=dc_code,
        dc_title=dc_title,
        will_measure=will_measure,
        describe_to_examiner=describe,
        records_to_bring=records_to_bring,
        notes=notes,
    )


def persist_exam_prep(
    driver: GraphDriver, exam_prep: ExamPrep, claim_id: str | None = None
) -> str:
    """Persist as a ``:User:ExamPrep`` node, attached to the ClaimedCondition
    for ``dc_code`` (when a claim_id is supplied). Returns the new node id.

    Persists a NEW node per call so versions are preserved (Review/Edit).
    """
    node_id = str(uuid4())
    next_version = exam_prep.version
    # Determine the next version number from the graph if there are prior nodes.
    prior = driver.user_read(
        exam_prep.user_id,
        """
        MATCH (ep:User:ExamPrep {user_id: $user_id, dc_code: $code})
        RETURN max(ep.version) AS max_v
        """,
        params={"code": exam_prep.dc_code},
    )
    if prior and prior[0]["max_v"]:
        next_version = prior[0]["max_v"] + 1

    driver.user_write(
        exam_prep.user_id,
        """
        MERGE (ep:User:ExamPrep {id: $id})
          SET ep.user_id = $user_id,
              ep.dc_code = $code,
              ep.dc_title = $title,
              ep.version = $version,
              ep.will_measure = $will_measure,
              ep.describe_to_examiner = $describe,
              ep.records_to_bring = $records,
              ep.notes = $notes,
              ep.generated_at = $now
        """,
        params={
            "id": node_id,
            "code": exam_prep.dc_code,
            "title": exam_prep.dc_title,
            "version": next_version,
            "will_measure": [
                f"{m['name']} ({m['body_part']}) — {m['unit']}" for m in exam_prep.will_measure
            ],
            "describe": exam_prep.describe_to_examiner,
            "records": exam_prep.records_to_bring,
            "notes": exam_prep.notes,
            "now": datetime.now().isoformat(timespec="seconds"),
        },
    )
    if claim_id:
        driver.user_write(
            exam_prep.user_id,
            """
            MATCH (cl:User:Claim {id: $claim_id, user_id: $user_id})
                  -[:CLAIMS]->(cc:User:ClaimedCondition {dc_code: $code, user_id: $user_id})
            MATCH (ep:User:ExamPrep {id: $id, user_id: $user_id})
            MERGE (cc)-[:HAS_EXAM_PREP]->(ep)
            """,
            params={"claim_id": claim_id, "code": exam_prep.dc_code, "id": node_id},
        )
    return node_id


def persist_lay_statement(
    driver: GraphDriver,
    user_id: str,
    *,
    claim_id: str,
    dc_code: str,
    text: str,
    factuality_ok: bool,
) -> str:
    """Persist a drafted Lay Statement as a versioned ``:User:LayStatement``
    node and attach it to the matching ``:User:ClaimedCondition``."""
    node_id = str(uuid4())
    prior = driver.user_read(
        user_id,
        """
        MATCH (ls:User:LayStatement {user_id: $user_id, dc_code: $code})
        RETURN max(ls.version) AS max_v
        """,
        params={"code": dc_code},
    )
    next_version = (prior[0]["max_v"] + 1) if prior and prior[0]["max_v"] else 1
    driver.user_write(
        user_id,
        """
        MERGE (ls:User:LayStatement {id: $id})
          SET ls.user_id = $user_id,
              ls.dc_code = $code,
              ls.version = $version,
              ls.text = $text,
              ls.factuality_ok = $ok,
              ls.generated_at = $now
        """,
        params={
            "id": node_id,
            "code": dc_code,
            "version": next_version,
            "text": text,
            "ok": factuality_ok,
            "now": datetime.now().isoformat(timespec="seconds"),
        },
    )
    driver.user_write(
        user_id,
        """
        MATCH (cl:User:Claim {id: $claim_id, user_id: $user_id})
              -[:CLAIMS]->(cc:User:ClaimedCondition {dc_code: $code, user_id: $user_id})
        MATCH (ls:User:LayStatement {id: $id, user_id: $user_id})
        MERGE (cc)-[:HAS_LAY_STATEMENT]->(ls)
        """,
        params={"claim_id": claim_id, "code": dc_code, "id": node_id},
    )
    return node_id
