"""Hybrid vector + graph retrieval (Pattern 3 GraphRAG).

Flow:
1. Read all the veteran's SymptomReports and MeasurementReports.
2. Build a search query per report.
3. Vector-search Criterion embeddings; collect hits + similarity scores.
4. Graph-traverse from each Criterion hit to its DiagnosticCode and RatingLevel.
5. For each candidate DC, also pick up *all* its criteria + measurements so the
   drafter has the full picture later.
6. Rank candidates by best-supporting Rating Percentage and the breadth of the
   veteran's evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..embeddings import (
    CRITERION_VECTOR_INDEX,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from ..graph.driver import GraphDriver
from ..graph.tools import get_measurement_reports, get_symptom_reports


@dataclass
class CriterionHit:
    text: str
    score: float


@dataclass
class CandidateDC:
    """One candidate Diagnostic Code the agent identified for the veteran."""

    code: str
    title: str
    body_system: str
    section: str
    best_percent: int
    criterion_hits: list[CriterionHit] = field(default_factory=list)
    matching_measurements: list[dict] = field(default_factory=list)
    matching_symptoms: list[dict] = field(default_factory=list)


def find_candidate_dcs(
    driver: GraphDriver,
    user_id: str,
    *,
    embedder: EmbeddingProvider | None = None,
    top_k_per_report: int = 5,
    similarity_floor: float = 0.55,
) -> list[CandidateDC]:
    """Return ranked CandidateDC list for the veteran's reports."""
    embedder = embedder or OpenAIEmbeddingProvider()

    symptom_reports = get_symptom_reports(driver, user_id)
    measurement_reports = get_measurement_reports(driver, user_id)

    queries: list[str] = []
    for sr in symptom_reports:
        # Build a search string that emphasises the body part + symptom text.
        parts = [sr["body_part"]]
        if sr.get("text"):
            parts.append(sr["text"])
        if sr.get("flareup_severity"):
            parts.append(f"flare-up severity {sr['flareup_severity']}")
        if sr.get("functional_loss"):
            parts.append("functional loss: " + ", ".join(sr["functional_loss"]))
        queries.append(" — ".join(p for p in parts if p))
    for mr in measurement_reports:
        queries.append(f"{mr['name']} {mr['body_part']} {mr['value']}{mr['unit']}")

    if not queries:
        return []

    embeddings = embedder.embed(queries)
    criterion_hits: dict[str, float] = {}  # criterion_text -> best score across reports
    for vec in embeddings:
        rows = driver.cfr_read(
            f"""
            CALL db.index.vector.queryNodes('{CRITERION_VECTOR_INDEX}', $top_k, $vec)
            YIELD node, score
            RETURN node.text AS text, score
            """,
            params={"vec": vec, "top_k": top_k_per_report},
        )
        for row in rows:
            if row["score"] < similarity_floor:
                continue
            text = row["text"]
            if text not in criterion_hits or row["score"] > criterion_hits[text]:
                criterion_hits[text] = row["score"]

    if not criterion_hits:
        return []

    # Now graph-traverse from criteria to DCs.
    dc_data: dict[str, dict] = {}
    for criterion_text, score in criterion_hits.items():
        rows = driver.cfr_read(
            """
            MATCH (c:CFR:Criterion {text: $text})<-[:REQUIRES]-(rl:RatingLevel)
            MATCH (c)-[:CRITERION_FOR]->(dc:CFR:DiagnosticCode)
            MATCH (dc)-[:IN_SECTION]->(s:Section)
            RETURN dc.code AS code, dc.title AS title, dc.body_system AS body_system,
                   s.id AS section, rl.percent AS percent
            """,
            params={"text": criterion_text},
        )
        for row in rows:
            d = dc_data.setdefault(
                row["code"],
                {
                    "title": row["title"],
                    "body_system": row["body_system"],
                    "section": row["section"],
                    "best_percent": -1,
                    "criterion_hits": [],
                },
            )
            d["best_percent"] = max(d["best_percent"], row["percent"])
            d["criterion_hits"].append(CriterionHit(text=criterion_text, score=score))

    # Pull measurements + symptoms that match within each candidate DC.
    measurement_matches = _match_measurements(driver, measurement_reports, list(dc_data.keys()))
    symptom_matches = _match_symptoms(driver, symptom_reports, list(dc_data.keys()))

    candidates: list[CandidateDC] = []
    for code, d in dc_data.items():
        candidates.append(
            CandidateDC(
                code=code,
                title=d["title"],
                body_system=d["body_system"],
                section=d["section"],
                best_percent=d["best_percent"],
                criterion_hits=sorted(d["criterion_hits"], key=lambda h: h.score, reverse=True),
                matching_measurements=measurement_matches.get(code, []),
                matching_symptoms=symptom_matches.get(code, []),
            )
        )

    candidates.sort(
        key=lambda c: (c.best_percent, len(c.criterion_hits), sum(h.score for h in c.criterion_hits)),
        reverse=True,
    )
    return candidates


def _match_measurements(
    driver: GraphDriver, reports: Sequence[dict], dc_codes: Sequence[str]
) -> dict[str, list[dict]]:
    """For each DC, find Measurement nodes whose threshold the veteran's reports satisfy.

    For ``≤`` thresholds we count a match when ``vet_value <= threshold``.
    For ``>=`` thresholds we count a match when ``vet_value >= threshold``.
    For ``=`` we use exact equality.
    """
    if not reports or not dc_codes:
        return {}
    out: dict[str, list[dict]] = {}
    for code in dc_codes:
        rows = driver.cfr_read(
            """
            MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl)-[:REQUIRES]->(c:Criterion)
                  -[:HAS_MEASUREMENT]->(m:Measurement)
            RETURN m.name AS name, m.body_part AS body_part,
                   m.operator AS operator, m.value AS threshold, m.unit AS unit,
                   rl.percent AS percent
            """,
            params={"code": code},
        )
        matches: list[dict] = []
        for vet_mr in reports:
            for m in rows:
                if m["name"] != vet_mr["name"] or m["body_part"] != vet_mr["body_part"]:
                    continue
                if m["unit"] != vet_mr["unit"]:
                    continue
                if _satisfies(vet_mr["value"], m["operator"], m["threshold"]):
                    matches.append(
                        {
                            "vet_value": vet_mr["value"],
                            "vet_unit": vet_mr["unit"],
                            "threshold": m["threshold"],
                            "operator": m["operator"],
                            "name": m["name"],
                            "body_part": m["body_part"],
                            "supports_percent": m["percent"],
                        }
                    )
        if matches:
            out[code] = matches
    return out


def _match_symptoms(
    driver: GraphDriver, reports: Sequence[dict], dc_codes: Sequence[str]
) -> dict[str, list[dict]]:
    """For each DC, find Symptom nodes that match veteran's reported symptoms by body_part."""
    if not reports or not dc_codes:
        return {}
    out: dict[str, list[dict]] = {}
    body_parts = [r["body_part"] for r in reports]
    for code in dc_codes:
        rows = driver.cfr_read(
            """
            MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl)-[:REQUIRES]->(c:Criterion)
                  -[:HAS_SYMPTOM]->(s:Symptom)
            WHERE s.body_part IN $parts
            RETURN s.name AS name, s.body_part AS body_part, rl.percent AS percent
            """,
            params={"parts": body_parts, "code": code},
        )
        if rows:
            out[code] = [dict(r) for r in rows]
    return out


def _satisfies(vet_value: float, operator: str, threshold: float) -> bool:
    op = operator.replace("≤", "<=").replace("≥", ">=")
    if op == "<=":
        return vet_value <= threshold
    if op == "<":
        return vet_value < threshold
    if op == "=":
        return vet_value == threshold
    if op == ">":
        return vet_value > threshold
    if op == ">=":
        return vet_value >= threshold
    return False
