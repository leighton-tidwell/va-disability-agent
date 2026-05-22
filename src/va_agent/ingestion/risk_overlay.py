"""Apply the hand-curated `mos_risk.yaml` overlay onto the JobCode spine.

The overlay attaches ``:RISK_FOR {confidence, source}`` edges from existing
``:JobCode`` nodes to ``:Anatomy`` and ``:Symptom`` nodes. By construction the
overlay cannot invent JobCodes — if an entry references a code that does not
already exist in the spine (built by ``jobcodes.ingest_job_code_spine``), we
raise ``OverlayError`` and skip the entry.

YAML schema (one entry per JobCode):

    - code: "15T"
      branch: "Army"
      title: "UH-60 Helicopter Repairer"   # informational only
      likely_anatomy: ["knee", "back", "shoulder"]
      likely_symptoms: ["chronic-back-pain"]
      rationale: "..."
      sources:
        - { type: "duty-description-inference", confidence: "medium" }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..graph.driver import GraphDriver


class OverlayError(RuntimeError):
    """Raised when an overlay entry references a JobCode that doesn't exist in
    the spine, or when YAML validation fails."""


class RiskSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    confidence: str  # "low" | "medium" | "high"


class RiskEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    branch: str
    title: str | None = None
    likely_anatomy: list[str] = Field(default_factory=list)
    likely_symptoms: list[str] = Field(default_factory=list)
    rationale: str
    sources: list[RiskSource] = Field(min_length=1)


@dataclass
class OverlayReport:
    entries_seen: int = 0
    entries_applied: int = 0
    risk_edges_written: int = 0
    missing_job_codes: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- anatomy normalisation ----------------------------------------------------

# Map free-text anatomy names from the overlay onto canonical (name, body_system)
# tuples. The body_system list mirrors CONTEXT.md.
_ANATOMY_BODY_SYSTEM: dict[str, str] = {
    "knee": "musculoskeletal",
    "back": "musculoskeletal",
    "lumbar spine": "musculoskeletal",
    "cervical spine": "musculoskeletal",
    "thoracic spine": "musculoskeletal",
    "shoulder": "musculoskeletal",
    "hip": "musculoskeletal",
    "ankle": "musculoskeletal",
    "wrist": "musculoskeletal",
    "elbow": "musculoskeletal",
    "foot": "musculoskeletal",
    "neck": "musculoskeletal",
    "hearing": "hearing",
    "tinnitus": "hearing",
}


def _anatomy_body_system(name: str) -> str:
    return _ANATOMY_BODY_SYSTEM.get(name.lower(), "musculoskeletal")


# --- graph operations ---------------------------------------------------------


def _job_code_exists(driver: GraphDriver, code: str, branch: str) -> bool:
    rows = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: $code, branch: $branch})
        RETURN jc.code AS code LIMIT 1
        """,
        code=code,
        branch=branch,
    )
    return bool(rows)


def _write_anatomy_risk(
    driver: GraphDriver,
    *,
    code: str,
    branch: str,
    anatomy: str,
    confidence: str,
    source: str,
) -> None:
    driver.cfr_write(
        """
        MATCH (jc:CFR:JobCode {code: $code, branch: $branch})
        MERGE (a:CFR:Anatomy {name: $anatomy})
          ON CREATE SET a.body_system = $body_system,
                        a.side = $side
        MERGE (jc)-[r:RISK_FOR {target_kind: 'anatomy'}]->(a)
          SET r.confidence = $confidence,
              r.source     = $source
        """,
        code=code,
        branch=branch,
        anatomy=anatomy.lower(),
        body_system=_anatomy_body_system(anatomy),
        side="unspecified",
        confidence=confidence,
        source=source,
    )


def _write_symptom_risk(
    driver: GraphDriver,
    *,
    code: str,
    branch: str,
    symptom: str,
    confidence: str,
    source: str,
) -> None:
    driver.cfr_write(
        """
        MATCH (jc:CFR:JobCode {code: $code, branch: $branch})
        MERGE (s:CFR:Symptom {name: $symptom})
          ON CREATE SET s.body_part = $body_part
        MERGE (jc)-[r:RISK_FOR {target_kind: 'symptom'}]->(s)
          SET r.confidence = $confidence,
              r.source     = $source
        """,
        code=code,
        branch=branch,
        symptom=symptom.lower(),
        body_part=_symptom_body_part(symptom),
        confidence=confidence,
        source=source,
    )


def _symptom_body_part(symptom: str) -> str:
    """Best-effort body part inference from the symptom slug."""
    s = symptom.lower()
    for part in ("back", "knee", "shoulder", "hip", "ankle", "neck", "wrist", "elbow", "foot"):
        if part in s:
            return part
    return "unspecified"


# --- YAML loading -------------------------------------------------------------


def load_overlay(yaml_path: Path | str) -> list[RiskEntry]:
    """Parse and validate the overlay file. Raises OverlayError on bad shape."""
    yaml_path = Path(yaml_path)
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise OverlayError(f"{yaml_path}: top-level YAML must be a list")
    entries: list[RiskEntry] = []
    for i, item in enumerate(raw):
        try:
            entries.append(RiskEntry.model_validate(item))
        except ValidationError as exc:
            raise OverlayError(f"{yaml_path}: entry {i} invalid: {exc}") from exc
    return entries


# --- public entry point -------------------------------------------------------


def apply_mos_risk_overlay(
    yaml_path: Path | str,
    driver: GraphDriver,
    *,
    strict: bool = True,
) -> OverlayReport:
    """Load the overlay YAML and attach RISK_FOR edges to existing JobCodes.

    Args:
        yaml_path: Path to the overlay file.
        driver: GraphDriver wrapping a live Neo4j instance.
        strict: When True (default), raise OverlayError on the first entry that
            references a JobCode missing from the spine. When False, log the
            miss in ``report.missing_job_codes`` and continue.
    """
    entries = load_overlay(yaml_path)
    report = OverlayReport()

    for entry in entries:
        report.entries_seen += 1
        if not _job_code_exists(driver, entry.code, entry.branch):
            if strict:
                raise OverlayError(
                    f"Overlay references unknown JobCode {entry.branch}/{entry.code}"
                    " — refusing to invent JobCodes."
                )
            report.missing_job_codes.append((entry.branch, entry.code))
            continue

        # Pick the first source as the canonical confidence/source for the edges.
        # (If multiple sources are listed, downstream callers can re-read the
        # YAML for the full set.)
        primary = entry.sources[0]
        for anatomy in entry.likely_anatomy:
            _write_anatomy_risk(
                driver,
                code=entry.code,
                branch=entry.branch,
                anatomy=anatomy,
                confidence=primary.confidence,
                source=primary.type,
            )
            report.risk_edges_written += 1
        for symptom in entry.likely_symptoms:
            _write_symptom_risk(
                driver,
                code=entry.code,
                branch=entry.branch,
                symptom=symptom,
                confidence=primary.confidence,
                source=primary.type,
            )
            report.risk_edges_written += 1
        report.entries_applied += 1

    return report


__all__ = [
    "OverlayError",
    "OverlayReport",
    "RiskEntry",
    "RiskSource",
    "apply_mos_risk_overlay",
    "load_overlay",
]
