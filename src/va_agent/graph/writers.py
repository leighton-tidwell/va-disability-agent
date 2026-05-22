"""Write validated CFR extractions to the graph.

Each function is idempotent (uses ``MERGE``) so re-ingestion of the same
content produces the same graph state. ``retrieved_at`` and ``content_hash``
are stored on every CFR node so slice #13 can diff.
"""

from __future__ import annotations

from datetime import date

from ..ingestion.schemas import DiagnosticCodeExtraction
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
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
