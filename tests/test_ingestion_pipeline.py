"""End-to-end ingestion test using a fake extractor.

Drives the orchestrator with a hand-crafted DiagnosticCodeExtraction so the
test stays hermetic (no OpenAI calls), yet exercises the validator + writer +
graph round trip.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.ingestion.cfr import ingest_diagnostic_code
from va_agent.ingestion.extraction import StructuredExtractor
from va_agent.ingestion.schemas import (
    CriterionExtraction,
    DiagnosticCodeExtraction,
    MeasurementExtraction,
    RatingLevelExtraction,
)


class FakeExtractor(StructuredExtractor):
    def __init__(self, extraction: DiagnosticCodeExtraction) -> None:
        self.extraction = extraction
        self.called_with: list[str] = []

    def extract(self, raw_text: str) -> DiagnosticCodeExtraction:
        self.called_with.append(raw_text)
        return self.extraction.model_copy(update={"raw_text": raw_text})


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


@pytest.fixture
def cleanup_dc(driver):
    """Remove the test DC before and after to keep the test idempotent."""
    code = "5260"
    queries = [
        # Detach DC and its dependent nodes from the test code.
        "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)-[:REQUIRES]->(c:Criterion)-[:HAS_MEASUREMENT]->(m:Measurement) DETACH DELETE m",
        "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)-[:REQUIRES]->(c:Criterion) DETACH DELETE c",
        "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:CROSS_REFERENCES]->(x) DETACH DELETE x",
        "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_NOTE]->(n) DETACH DELETE n",
        "MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc",
        # Section may also exist; leave it for now.
    ]
    for q in queries:
        driver.cfr_write(q, code=code)
    yield
    for q in queries:
        driver.cfr_write(q, code=code)


def _build_fake_extraction() -> DiagnosticCodeExtraction:
    return DiagnosticCodeExtraction(
        code="5260",
        title="Leg, limitation of flexion of",
        body_system="musculoskeletal",
        section="4.71a",
        rating_levels=[
            RatingLevelExtraction(
                percent=10,
                criteria=[
                    CriterionExtraction(
                        text="Flexion limited to 45°",
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
            ),
            RatingLevelExtraction(
                percent=20,
                criteria=[
                    CriterionExtraction(
                        text="Flexion limited to 30°",
                        measurements=[
                            MeasurementExtraction(
                                name="flexion",
                                body_part="knee",
                                operator="<=",
                                value=30,
                                unit="degrees",
                            )
                        ],
                    )
                ],
            ),
        ],
        cross_references=["DC 5003"],
        notes=[],
        raw_text="",  # will be filled in by the orchestrator
    )


def test_ingest_dc_5260_with_fake_extractor(driver, cleanup_dc, monkeypatch, tmp_path):
    fake = FakeExtractor(_build_fake_extraction())

    fake_xml = (
        "<DIV1><DIV5><DIV8>"
        "<P>5260 Leg, limitation of flexion of:</P>"
        "<P>Flexion limited to 45° — 10%</P>"
        "<P>Flexion limited to 30° — 20%</P>"
        "</DIV8></DIV5></DIV1>"
    ).encode("utf-8")

    monkeypatch.setattr(
        "va_agent.ingestion.cfr.fetch_section_xml",
        lambda **kwargs: (fake_xml.decode("utf-8"), tmp_path / "fake.xml"),
    )

    report = ingest_diagnostic_code(
        section="4.71a",
        dc_code="5260",
        driver=driver,
        extractor=fake,
        retrieval_date=date(2026, 5, 22),
    )

    assert "5260" in report.dc_codes_written, report
    assert report.dc_codes_failed == []
    assert fake.called_with, "extractor was never invoked"

    rows = driver.cfr_read(
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(rl:RatingLevel)
        RETURN dc.title AS title, rl.percent AS percent
        ORDER BY percent
        """,
        code="5260",
    )
    assert rows == [
        {"title": "Leg, limitation of flexion of", "percent": 10},
        {"title": "Leg, limitation of flexion of", "percent": 20},
    ]

    measurements = driver.cfr_read(
        """
        MATCH (m:CFR:Measurement)
        WHERE m.name = 'flexion' AND m.body_part = 'knee'
        RETURN m.value AS value, m.unit AS unit
        ORDER BY value
        """,
    )
    assert {(row["value"], row["unit"]) for row in measurements} == {
        (30.0, "degrees"),
        (45.0, "degrees"),
    }
