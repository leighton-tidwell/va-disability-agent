"""End-to-end test for ``ingest_section_full`` with a fake extractor.

Drives the orchestrator against the real cached §4.71a XML — so DC discovery
is exercised against real source — but stubs the LLM extractor so the test is
hermetic and fast. A slow-marked live test (gated on an env var) is included
for manual runs against OpenAI.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.ingestion.cfr import ingest_section_full
from va_agent.ingestion.discovery import discover_dc_codes
from va_agent.ingestion.extraction import StructuredExtractor
from va_agent.ingestion.schemas import (
    CriterionExtraction,
    DiagnosticCodeExtraction,
    MeasurementExtraction,
    RatingLevelExtraction,
)

CACHED_XML = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ecfr_cache"
    / "2025-01-01-t38-p4-s4.71a.xml"
)


class StubDCExtractor(StructuredExtractor):
    """Returns a minimal valid DiagnosticCodeExtraction for any DC code.

    The raw text passed in always starts with the 4-digit DC code, so we can
    recover the code without any actual LLM call.
    """

    def __init__(self) -> None:
        self.codes_seen: list[str] = []

    def extract(self, raw_text: str) -> DiagnosticCodeExtraction:
        code = raw_text.lstrip()[:4]
        self.codes_seen.append(code)
        return DiagnosticCodeExtraction(
            code=code,
            title=f"Stub DC {code}",
            body_system="musculoskeletal",
            section="4.71a",
            rating_levels=[
                RatingLevelExtraction(
                    percent=10,
                    criteria=[
                        CriterionExtraction(
                            text="stub criterion",
                            measurements=[
                                MeasurementExtraction(
                                    name="flexion",
                                    body_part="knee",
                                    operator="<=",
                                    value=45,
                                    unit="degrees",
                                )
                            ],
                        )
                    ],
                )
            ],
            cross_references=[],
            notes=[],
            raw_text=raw_text,
        )


@pytest.fixture(scope="module")
def driver():
    try:
        d = GraphDriver.from_env()
        with d.session() as s:
            s.run("RETURN 1").consume()
    except (ServiceUnavailable, OSError) as exc:
        pytest.skip(f"Neo4j not reachable: {exc}")
    yield d
    d.close()


@pytest.fixture(scope="module")
def cached_codes() -> list[str]:
    if not CACHED_XML.exists():
        pytest.skip(f"cached §4.71a XML missing at {CACHED_XML}")
    return discover_dc_codes(CACHED_XML.read_text(encoding="utf-8"))


@pytest.fixture
def cleanup_stubs(driver, cached_codes):
    """Remove every DC we're about to write so the test is idempotent and
    leaves the graph clean for downstream tests.
    """

    def _cleanup():
        for code in cached_codes:
            for q in [
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)-[:REQUIRES]->(c:Criterion)-[:HAS_MEASUREMENT]->(m:Measurement) DETACH DELETE m",
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)-[:REQUIRES]->(c:Criterion) DETACH DELETE c",
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:CROSS_REFERENCES]->(x) DETACH DELETE x",
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_NOTE]->(n) DETACH DELETE n",
                "MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc",
            ]:
                driver.cfr_write(q, code=code)
        # Anatomy cleanup — only nodes we created.
        driver.cfr_write(
            "MATCH (a:CFR:Anatomy {name: 'knee'}) DETACH DELETE a",
        )

    _cleanup()
    yield
    _cleanup()


def test_ingest_section_full_writes_all_discovered_dcs(
    driver, cleanup_stubs, cached_codes
):
    stub = StubDCExtractor()
    report = ingest_section_full(
        section="4.71a",
        driver=driver,
        extractor=stub,
        retrieval_date=date(2025, 1, 1),
        # Skip anatomy/xref post-pass during the bulk write; tested separately.
        enrich_anatomy=False,
        resolve_xrefs=False,
    )

    assert set(report.dc_codes_written) == set(cached_codes), (
        f"missing: {set(cached_codes) - set(report.dc_codes_written)}, "
        f"extra: {set(report.dc_codes_written) - set(cached_codes)}"
    )
    assert report.dc_codes_failed == []

    rows = driver.cfr_read(
        "MATCH (dc:CFR:DiagnosticCode) WHERE dc.code IN $codes RETURN count(dc) AS n",
        codes=cached_codes,
    )
    assert rows[0]["n"] == len(cached_codes)


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_INGESTION") != "1",
    reason="live OpenAI ingestion is opt-in (set RUN_LIVE_INGESTION=1)",
)
def test_ingest_section_full_live_4_71a(driver, cleanup_stubs, cached_codes):
    """Slow / costly: drives the real OpenAI extractor over every DC in §4.71a.

    Gate this behind RUN_LIVE_INGESTION=1 so default ``pytest`` runs stay fast
    and never burn API tokens unexpectedly.
    """
    report = ingest_section_full(
        section="4.71a",
        driver=driver,
        retrieval_date=date(2025, 1, 1),
    )
    # Sanity floor: we should write the bulk of the discovered DCs. Some may
    # legitimately fall into the review queue.
    assert len(report.dc_codes_written) >= int(0.7 * len(cached_codes)), report
