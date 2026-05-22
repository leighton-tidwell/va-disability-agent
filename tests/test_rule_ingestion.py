"""End-to-end rule-ingestion test with a fake RuleExtractor."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.graph.writers import resolve_cross_references
from va_agent.ingestion.cfr import ingest_rule_section
from va_agent.ingestion.extraction import RuleExtractor, extract_section_text
from va_agent.ingestion.schemas import RuleExtraction


class FakeRuleExtractor(RuleExtractor):
    def __init__(self, rule: RuleExtraction) -> None:
        self.rule = rule
        self.calls: list[tuple[str, str]] = []

    def extract(self, raw_text: str, *, section: str) -> RuleExtraction:
        self.calls.append((section, raw_text))
        return self.rule


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
def cleanup_rule(driver):
    rule_id = "pyramiding"
    driver.cfr_write("MATCH (r:CFR:Rule {id: $id}) DETACH DELETE r", id=rule_id)
    yield rule_id
    driver.cfr_write("MATCH (r:CFR:Rule {id: $id}) DETACH DELETE r", id=rule_id)


SAMPLE_SECTION_XML = (
    "<DIV8 N='4.14' TYPE='SECTION'>"
    "<HEAD>§ 4.14 Avoidance of pyramiding.</HEAD>"
    "<P>The evaluation of the same disability under various diagnoses is to be "
    "avoided. Both the use of manifestations not resulting from service-connected "
    "disease or injury in establishing the service-connected evaluation, and the "
    "evaluation of the same manifestation under different diagnoses are to be "
    "avoided.</P>"
    "</DIV8>"
)


def test_extract_section_text_pulls_heading_and_body():
    heading, body = extract_section_text(SAMPLE_SECTION_XML)
    assert "Avoidance of pyramiding" in heading
    assert "same disability" in body


def test_ingest_rule_section_writes_node(driver, cleanup_rule, monkeypatch, tmp_path):
    fake = FakeRuleExtractor(
        RuleExtraction(
            id="pyramiding",
            name="Pyramiding",
            text=(
                "The evaluation of the same disability under various diagnoses "
                "is to be avoided. Both the use of manifestations not resulting "
                "from service-connected disease or injury in establishing the "
                "service-connected evaluation, and the evaluation of the same "
                "manifestation under different diagnoses are to be avoided."
            ),
            body_system="general",
            section="4.14",
            applies_to=[],
        )
    )

    monkeypatch.setattr(
        "va_agent.ingestion.cfr.fetch_section_xml",
        lambda **kwargs: (SAMPLE_SECTION_XML, tmp_path / "fake.xml"),
    )

    report = ingest_rule_section(
        section="4.14",
        driver=driver,
        extractor=fake,
        retrieval_date=date(2026, 5, 22),
    )

    assert "pyramiding" in report.rules_written, report
    assert report.rules_failed == []

    rows = driver.cfr_read(
        """
        MATCH (r:CFR:Rule {id: 'pyramiding'})-[:IN_SECTION]->(s:CFR:Section)
        RETURN r.name AS name, r.section AS section, r.body_system AS bs, s.id AS sid
        """,
    )
    assert rows == [
        {
            "name": "Pyramiding",
            "section": "4.14",
            "bs": "general",
            "sid": "4.14",
        }
    ]


def test_resolve_cross_references_to_section(driver, cleanup_rule, monkeypatch, tmp_path):
    """If a Rule lives at §4.14 and a CrossReference targets '§4.14', the
    post-pass should wire :RESOLVES_TO from the xref to the rule."""
    fake = FakeRuleExtractor(
        RuleExtraction(
            id="pyramiding",
            name="Pyramiding",
            text="The evaluation of the same disability under various diagnoses is to be avoided. " * 2,
            body_system="general",
            section="4.14",
            applies_to=[],
        )
    )
    monkeypatch.setattr(
        "va_agent.ingestion.cfr.fetch_section_xml",
        lambda **kwargs: (SAMPLE_SECTION_XML, tmp_path / "fake.xml"),
    )
    ingest_rule_section(section="4.14", driver=driver, extractor=fake)

    # Seed a CrossReference pointing at §4.14.
    driver.cfr_write("MATCH (x:CFR:CrossReference {target: '§4.14'}) DETACH DELETE x")
    driver.cfr_write("MERGE (x:CFR:CrossReference {target: '§4.14'})")
    try:
        result = resolve_cross_references(driver)
        assert result["resolved"] >= 1, result
        rows = driver.cfr_read(
            """
            MATCH (x:CFR:CrossReference {target: '§4.14'})-[:RESOLVES_TO]->(r:CFR:Rule)
            RETURN r.id AS id
            """,
        )
        assert rows == [{"id": "pyramiding"}]
    finally:
        driver.cfr_write("MATCH (x:CFR:CrossReference {target: '§4.14'}) DETACH DELETE x")
