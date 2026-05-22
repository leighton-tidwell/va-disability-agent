"""Write validated CFR extractions to the graph.

Each function is idempotent (uses ``MERGE``) so re-ingestion of the same
content produces the same graph state. ``retrieved_at`` and ``content_hash``
are stored on every CFR node so slice #13 can diff.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from ..ingestion.schemas import DiagnosticCodeExtraction, RuleExtraction
from .driver import GraphDriver


def write_diagnostic_code(
    driver: GraphDriver,
    extraction: DiagnosticCodeExtraction,
    *,
    content_hash: str,
    retrieved_at: date | None = None,
) -> None:
    """Materialise a DiagnosticCodeExtraction as Section + DC + RatingLevels +
    Criteria + Measurements nodes and edges in the graph.
    """
    retrieved_at = retrieved_at or date.today()
    retrieved_at_iso = retrieved_at.isoformat()

    driver.cfr_write(
        """
        MERGE (s:CFR:Section {id: $section})
          ON CREATE SET s.retrieved_at = $retrieved_at
        MERGE (dc:CFR:DiagnosticCode {code: $code})
          SET dc.title         = $title,
              dc.body_system   = $body_system,
              dc.raw_text      = $raw_text,
              dc.content_hash  = $content_hash,
              dc.retrieved_at  = $retrieved_at,
              dc.source_citation = $citation
        MERGE (dc)-[:IN_SECTION]->(s)
        """,
        section=extraction.section,
        code=extraction.code,
        title=extraction.title,
        body_system=extraction.body_system,
        raw_text=extraction.raw_text,
        content_hash=content_hash,
        retrieved_at=retrieved_at_iso,
        citation=f"38 CFR §{extraction.section} DC {extraction.code}",
    )

    for level in extraction.rating_levels:
        driver.cfr_write(
            """
            MERGE (dc:CFR:DiagnosticCode {code: $code})
            MERGE (rl:CFR:RatingLevel {percent: $percent})
            MERGE (dc)-[:HAS_RATING]->(rl)
            """,
            code=extraction.code,
            percent=level.percent,
        )
        for criterion in level.criteria:
            driver.cfr_write(
                """
                MATCH (dc:CFR:DiagnosticCode {code: $code})
                MATCH (rl:CFR:RatingLevel {percent: $percent})
                MERGE (dc)-[:HAS_RATING]->(rl)
                MERGE (c:CFR:Criterion {text: $text})
                  ON CREATE SET c.content_hash = $hash
                MERGE (c)-[:CRITERION_FOR]->(dc)
                MERGE (rl)-[:REQUIRES]->(c)
                """,
                code=extraction.code,
                percent=level.percent,
                text=criterion.text,
                hash=_text_hash(criterion.text),
            )
            for m in criterion.measurements:
                driver.cfr_write(
                    """
                    MATCH (c:CFR:Criterion {text: $text})
                    MERGE (m:CFR:Measurement {
                        name: $name, body_part: $body_part,
                        operator: $operator, value: $value, unit: $unit
                    })
                    MERGE (c)-[:HAS_MEASUREMENT]->(m)
                    MERGE (a:CFR:Anatomy {name: $body_part})
                    MERGE (m)-[:OF_ANATOMY]->(a)
                    """,
                    text=criterion.text,
                    name=m.name,
                    body_part=m.body_part,
                    operator=m.operator,
                    value=float(m.value),
                    unit=m.unit,
                )

    for ref in extraction.cross_references:
        driver.cfr_write(
            """
            MATCH (dc:CFR:DiagnosticCode {code: $code})
            MERGE (xref:CFR:CrossReference {target: $ref})
            MERGE (dc)-[:CROSS_REFERENCES]->(xref)
            """,
            code=extraction.code,
            ref=ref,
        )

    for i, note in enumerate(extraction.notes, start=1):
        driver.cfr_write(
            """
            MATCH (dc:CFR:DiagnosticCode {code: $code})
            MERGE (n:CFR:Note {dc_code: $code, ordinal: $i})
              SET n.text = $text
            MERGE (dc)-[:HAS_NOTE]->(n)
            """,
            code=extraction.code,
            i=i,
            text=note,
        )


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def write_rule(
    driver: GraphDriver,
    extraction: RuleExtraction,
    *,
    content_hash: str,
    retrieved_at: date | None = None,
) -> None:
    """Materialise a :CFR:Rule node (general provisions §4.1–§4.31).

    Rules carry the same provenance properties as Diagnostic Codes
    (``content_hash``, ``retrieved_at``, ``source_citation``) so slice #13's
    refresh-diff logic treats them uniformly. ``applies_to`` is stored as a
    string list property — fan-out to typed edges is left for a later slice.
    """
    retrieved_at = retrieved_at or date.today()
    retrieved_at_iso = retrieved_at.isoformat()

    driver.cfr_write(
        """
        MERGE (s:CFR:Section {id: $section})
          ON CREATE SET s.retrieved_at = $retrieved_at
        MERGE (r:CFR:Rule {id: $id})
          SET r.name           = $name,
              r.text           = $text,
              r.body_system    = $body_system,
              r.section        = $section,
              r.applies_to     = $applies_to,
              r.content_hash   = $content_hash,
              r.retrieved_at   = $retrieved_at,
              r.source_citation = $citation
        MERGE (r)-[:IN_SECTION]->(s)
        """,
        section=extraction.section,
        id=extraction.id,
        name=extraction.name,
        text=extraction.text,
        body_system=extraction.body_system,
        applies_to=list(extraction.applies_to),
        content_hash=content_hash,
        retrieved_at=retrieved_at_iso,
        citation=f"38 CFR §{extraction.section}",
    )


# --- Anatomy enrichment ----------------------------------------------------


@dataclass(frozen=True)
class AnatomyEntry:
    name: str
    body_system: str
    parent_anatomy: str
    side: str  # "left" | "right" | "unspecified"


@dataclass(frozen=True)
class AnatomyRegistry:
    entries: dict[str, AnatomyEntry]
    pairs: list[str]  # parent_anatomy names that have left/right pairs


def load_anatomy_registry(path: Path | None = None) -> AnatomyRegistry:
    """Read ``data/anatomy.yaml`` and return an in-memory registry."""
    if path is None:
        path = Path(__file__).resolve().parents[3] / "data" / "anatomy.yaml"
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    entries: dict[str, AnatomyEntry] = {}
    for row in raw.get("anatomy", []):
        entry = AnatomyEntry(
            name=row["name"].lower().strip(),
            body_system=row["body_system"],
            parent_anatomy=row["parent_anatomy"],
            side=row["side"],
        )
        entries[entry.name] = entry
    pairs = list(raw.get("pairs", []))
    return AnatomyRegistry(entries=entries, pairs=pairs)


def apply_anatomy_metadata(
    driver: GraphDriver,
    registry: AnatomyRegistry | None = None,
) -> dict[str, int]:
    """Post-pass: enrich :CFR:Anatomy nodes with body_system/side/parent and
    create :PAIRED_WITH edges for known contralateral pairs.

    Idempotent. Returns a small report ``{"enriched": N, "paired": M}`` so the
    caller can confirm the pass ran.
    """
    registry = registry or load_anatomy_registry()

    # Enrich every Anatomy node whose name matches a registry entry.
    enriched = 0
    for entry in registry.entries.values():
        result = driver.cfr_read(
            """
            MATCH (a:CFR:Anatomy)
            WHERE toLower(a.name) = $name
            SET a.body_system    = $body_system,
                a.parent_anatomy = $parent_anatomy,
                a.side           = $side
            RETURN count(a) AS n
            """,
            name=entry.name,
            body_system=entry.body_system,
            parent_anatomy=entry.parent_anatomy,
            side=entry.side,
        )
        enriched += int(result[0]["n"]) if result else 0

    # Default any Anatomy node we didn't recognise to side="unspecified" so the
    # property exists everywhere — query code can rely on it being non-null.
    driver.cfr_write(
        """
        MATCH (a:CFR:Anatomy)
        WHERE a.side IS NULL
        SET a.side = 'unspecified'
        """,
    )

    # Wire up :PAIRED_WITH between contralateral Anatomy nodes.
    paired = 0
    for parent in registry.pairs:
        rows = driver.cfr_read(
            """
            MATCH (l:CFR:Anatomy {parent_anatomy: $parent, side: 'left'})
            MATCH (r:CFR:Anatomy {parent_anatomy: $parent, side: 'right'})
            MERGE (l)-[:PAIRED_WITH]->(r)
            MERGE (r)-[:PAIRED_WITH]->(l)
            RETURN count(*) AS n
            """,
            parent=parent,
        )
        paired += int(rows[0]["n"]) if rows else 0

    return {"enriched": enriched, "paired": paired}


# --- Cross-reference resolution -------------------------------------------


_DC_REF_RE = re.compile(r"DC\s*(\d{4})")
_SECTION_REF_RE = re.compile(r"§\s*(\d+\.\d+[a-z]?)")


def resolve_cross_references(driver: GraphDriver) -> dict[str, int]:
    """Post-pass: turn :CFR:CrossReference nodes into typed edges where the
    target exists as a DC, Rule, or Section node.

    Creates ``(:CrossReference)-[:RESOLVES_TO]->(target)`` edges. Unresolved
    references stay as bare CrossReference nodes; callers can re-run this
    after additional ingestion brings new targets online.
    """
    refs = driver.cfr_read(
        """
        MATCH (x:CFR:CrossReference)
        RETURN x.target AS target
        """,
    )

    resolved = 0
    for row in refs:
        target = (row["target"] or "").strip()
        if not target:
            continue
        # DC reference
        dc_m = _DC_REF_RE.search(target)
        if dc_m:
            code = dc_m.group(1)
            res = driver.cfr_read(
                """
                MATCH (x:CFR:CrossReference {target: $target})
                MATCH (dc:CFR:DiagnosticCode {code: $code})
                MERGE (x)-[:RESOLVES_TO]->(dc)
                RETURN count(dc) AS n
                """,
                target=target,
                code=code,
            )
            resolved += int(res[0]["n"]) if res else 0
            continue
        # Section reference — try Rule.section first, then bare Section.
        sec_m = _SECTION_REF_RE.search(target)
        if sec_m:
            section = sec_m.group(1)
            res = driver.cfr_read(
                """
                MATCH (x:CFR:CrossReference {target: $target})
                OPTIONAL MATCH (r:CFR:Rule {section: $section})
                OPTIONAL MATCH (s:CFR:Section {id: $section})
                FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
                    MERGE (x)-[:RESOLVES_TO]->(r)
                )
                FOREACH (_ IN CASE WHEN r IS NULL AND s IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (x)-[:RESOLVES_TO]->(s)
                )
                RETURN (CASE WHEN r IS NOT NULL OR s IS NOT NULL THEN 1 ELSE 0 END) AS n
                """,
                target=target,
                section=section,
            )
            resolved += int(res[0]["n"]) if res else 0

    return {"resolved": resolved, "total": len(refs)}
