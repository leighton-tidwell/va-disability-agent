"""Tests for the re-ingestion + staleness diff routine."""

from __future__ import annotations

from datetime import date

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.ingestion.cfr import ingest_diagnostic_code
from va_agent.ingestion.diff import (
    ChangedDC,
    DiffReport,
    NewDC,
    RemovedDC,
    UnchangedDC,
    apply_diff,
    diff_section,
    extract_only,
)
from va_agent.ingestion.extraction import StructuredExtractor
from va_agent.ingestion.schemas import (
    CriterionExtraction,
    DiagnosticCodeExtraction,
    MeasurementExtraction,
    RatingLevelExtraction,
)


# --- Pure dataclass tests (no Neo4j, no LLM) ------------------------------


def test_diff_report_has_changes_property():
    r = DiffReport(section="4.71a")
    assert r.has_changes is False

    r.unchanged.append(UnchangedDC(code="5260", content_hash="abc"))
    assert r.has_changes is False, "unchanged-only is not a change"

    r.changed.append(
        ChangedDC(
            code="5261",
            old_hash="aaa",
            new_hash="bbb",
            new_extraction=_minimal_extraction("5261"),
            raw_text="5261 ...",
        )
    )
    assert r.has_changes is True


def test_extract_only_returns_no_extraction_on_validation_failure():
    """A DC whose extracted body_system is bogus should land in `failed`,
    not `changed` — proving extract_only's validation gating."""

    class BadExtractor(StructuredExtractor):
        def extract(self, raw_text: str) -> DiagnosticCodeExtraction:
            return DiagnosticCodeExtraction(
                code="5260",
                title="t",
                body_system="not-a-system",  # invalid
                section="4.71a",
                rating_levels=[
                    RatingLevelExtraction(
                        percent=10,
                        criteria=[CriterionExtraction(text="x")],
                    )
                ],
                cross_references=[],
                notes=[],
                raw_text=raw_text,
            )

    fake_xml = (
        "<DIV1><DIV5><DIV8>"
        "<P>5260 Leg, limitation of flexion of:</P>"
        "<P>Flexion limited to 45° — 10%</P>"
        "</DIV8></DIV5></DIV1>"
    )
    result = extract_only(
        section="4.71a",
        dc_code="5260",
        xml_text=fake_xml,
        extractor=BadExtractor(),
    )
    assert result.extraction is None
    assert result.errors, "validation errors must be surfaced"


# --- Helpers --------------------------------------------------------------


def _minimal_extraction(code: str, criterion_text: str = "Flexion limited to 45°") -> DiagnosticCodeExtraction:
    return DiagnosticCodeExtraction(
        code=code,
        title=f"DC {code}",
        body_system="musculoskeletal",
        section="4.71a",
        rating_levels=[
            RatingLevelExtraction(
                percent=10,
                criteria=[
                    CriterionExtraction(
                        text=criterion_text,
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
        raw_text="",
    )


class _FixedExtractor(StructuredExtractor):
    """Returns a preset extraction regardless of raw_text."""

    def __init__(self, extraction: DiagnosticCodeExtraction) -> None:
        self.extraction = extraction

    def extract(self, raw_text: str) -> DiagnosticCodeExtraction:
        return self.extraction.model_copy(update={"raw_text": raw_text})


# --- Live-Neo4j tests -----------------------------------------------------


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
def cleanup(driver):
    """Wipe the test DCs + the test section before and after."""
    codes = ["5260", "5261", "9999"]

    def _wipe():
        for code in codes:
            for q in [
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(:CFR:RatingLevel)-[:REQUIRES]->(c:CFR:Criterion)-[:HAS_MEASUREMENT]->(m:CFR:Measurement) DETACH DELETE m",
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(:CFR:RatingLevel)-[:REQUIRES]->(c:CFR:Criterion) DETACH DELETE c",
                "MATCH (dc:CFR:DiagnosticCode {code: $code})<-[:CRITERION_FOR]-(c:CFR:Criterion) DETACH DELETE c",
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:CROSS_REFERENCES]->(x) DETACH DELETE x",
                "MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_NOTE]->(n) DETACH DELETE n",
                "MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc",
            ]:
                driver.cfr_write(q, code=code)
        # Leftover knee anatomy from earlier runs is harmless; leave it.

    _wipe()
    yield
    _wipe()


SINGLE_DC_XML = (
    "<DIV1><DIV5><DIV8>"
    "<TABLE>"
    "<TR><TD>5260 Leg, limitation of flexion of:</TD><TD></TD></TR>"
    "<TR><TD>Flexion limited to 45°</TD><TD>10</TD></TR>"
    "</TABLE>"
    "</DIV8></DIV5></DIV1>"
)


def test_diff_section_reports_unchanged_when_graph_matches(driver, cleanup, monkeypatch, tmp_path):
    extractor = _FixedExtractor(_minimal_extraction("5260"))

    monkeypatch.setattr(
        "va_agent.ingestion.diff.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )
    monkeypatch.setattr(
        "va_agent.ingestion.cfr.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )

    ingest_diagnostic_code(
        section="4.71a",
        dc_code="5260",
        driver=driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )

    report = diff_section(
        "4.71a",
        driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )
    assert [u.code for u in report.unchanged] == ["5260"]
    assert report.changed == []
    assert report.removed == []
    assert report.new == []
    assert report.failed == []


def test_diff_section_detects_changed_dc(driver, cleanup, monkeypatch, tmp_path):
    extractor = _FixedExtractor(_minimal_extraction("5260"))

    monkeypatch.setattr(
        "va_agent.ingestion.diff.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )
    monkeypatch.setattr(
        "va_agent.ingestion.cfr.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )

    ingest_diagnostic_code(
        section="4.71a",
        dc_code="5260",
        driver=driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )

    # Mutate the stored DC's content_hash directly so the diff sees drift.
    driver.cfr_write(
        "MATCH (dc:CFR:DiagnosticCode {code: '5260'}) SET dc.content_hash = 'stale-hash'",
    )

    report = diff_section(
        "4.71a",
        driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )
    assert [c.code for c in report.changed] == ["5260"]
    assert report.changed[0].old_hash == "stale-hash"
    assert report.unchanged == []
    assert report.removed == []
    assert report.new == []


def test_diff_section_detects_new_and_removed(driver, cleanup, monkeypatch, tmp_path):
    """Fresh XML has 5260; graph already has 5260 (unchanged) + 9999 (removed)."""
    extractor = _FixedExtractor(_minimal_extraction("5260"))

    monkeypatch.setattr(
        "va_agent.ingestion.diff.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )
    monkeypatch.setattr(
        "va_agent.ingestion.cfr.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )

    # Ingest 5260 normally so its hash matches the fresh XML.
    ingest_diagnostic_code(
        section="4.71a",
        dc_code="5260",
        driver=driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )
    # Seed an extra DC that the fresh XML doesn't contain.
    driver.cfr_write(
        """
        MERGE (s:CFR:Section {id: '4.71a'})
        MERGE (dc:CFR:DiagnosticCode {code: '9999'})
          SET dc.title='Phantom', dc.body_system='general', dc.raw_text='',
              dc.content_hash='phantom-hash', dc.retrieved_at='2025-01-01'
        MERGE (dc)-[:IN_SECTION]->(s)
        """,
    )

    report = diff_section(
        "4.71a",
        driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )
    assert [u.code for u in report.unchanged] == ["5260"]
    assert [r.code for r in report.removed] == ["9999"]
    assert report.changed == []
    assert report.new == []


def test_apply_diff_replaces_changed_dc(driver, cleanup, monkeypatch, tmp_path):
    extractor = _FixedExtractor(_minimal_extraction("5260", "Flexion limited to 45°"))

    monkeypatch.setattr(
        "va_agent.ingestion.diff.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )
    monkeypatch.setattr(
        "va_agent.ingestion.cfr.fetch_section_xml",
        lambda **kw: (SINGLE_DC_XML, tmp_path / "fake.xml"),
    )

    ingest_diagnostic_code(
        section="4.71a",
        dc_code="5260",
        driver=driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )

    # Mutate stored criterion text so we can verify apply_diff replaces it.
    driver.cfr_write(
        """
        MATCH (dc:CFR:DiagnosticCode {code: '5260'})-[:HAS_RATING]->(:CFR:RatingLevel)
              -[:REQUIRES]->(c:CFR:Criterion)
        SET c.text = 'CORRUPTED TEXT'
        """,
    )
    # Also bust the DC hash so the diff classifies it as changed.
    driver.cfr_write(
        "MATCH (dc:CFR:DiagnosticCode {code: '5260'}) SET dc.content_hash = 'stale-hash'",
    )

    report = diff_section(
        "4.71a",
        driver,
        extractor=extractor,
        retrieval_date=date(2025, 1, 1),
    )
    assert [c.code for c in report.changed] == ["5260"]

    apply_report = apply_diff(report, driver, confirm=True, retrieved_at=date(2025, 1, 1))
    assert apply_report.rewrote == ["5260"]

    # Verify the criterion is back to the canonical text.
    rows = driver.cfr_read(
        """
        MATCH (dc:CFR:DiagnosticCode {code: '5260'})-[:HAS_RATING]->(:CFR:RatingLevel)
              -[:REQUIRES]->(c:CFR:Criterion)
        RETURN c.text AS text
        """,
    )
    assert {r["text"] for r in rows} == {"Flexion limited to 45°"}

    # And there should be no orphan "CORRUPTED TEXT" criterion left over.
    orphans = driver.cfr_read(
        "MATCH (c:CFR:Criterion {text: 'CORRUPTED TEXT'}) RETURN count(c) AS n",
    )
    assert orphans[0]["n"] == 0


def test_apply_diff_without_confirm_raises():
    report = DiffReport(section="4.71a")
    with pytest.raises(ValueError):
        apply_diff(report, driver=None, confirm=False)  # type: ignore[arg-type]
