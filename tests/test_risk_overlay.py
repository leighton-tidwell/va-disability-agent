"""Tests for the MOS Risk Overlay.

Schema validation is hermetic. Graph behaviour (existing-JobCode check,
RISK_FOR edge writes, refusal to invent JobCodes) is live-Neo4j.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.ingestion.jobcodes import ingest_job_code_spine
from va_agent.ingestion.risk_overlay import (
    OverlayError,
    apply_mos_risk_overlay,
    load_overlay,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_XLSX = FIXTURE_DIR / "duty_mos_noise_fixture.xlsx"
FIXTURE_VALID = FIXTURE_DIR / "risk_overlay_valid.yaml"
FIXTURE_INVALID_REF = FIXTURE_DIR / "risk_overlay_invalid_ref.yaml"
FIXTURE_BAD_SCHEMA = FIXTURE_DIR / "risk_overlay_bad_schema.yaml"


# --- hermetic schema tests ---------------------------------------------------


def test_load_overlay_valid_schema():
    entries = load_overlay(FIXTURE_VALID)
    assert len(entries) == 2
    assert entries[0].code == "TEST-11B"
    assert entries[0].branch == "Army"
    assert entries[0].sources[0].confidence == "medium"


def test_load_overlay_rejects_missing_required_keys():
    with pytest.raises(OverlayError):
        load_overlay(FIXTURE_BAD_SCHEMA)


# --- live-Neo4j tests --------------------------------------------------------


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
def spine(driver):
    """Build the test spine, then tear it down."""

    def _cleanup():
        driver.cfr_write(
            """
            MATCH (jc:CFR:JobCode) WHERE jc.code STARTS WITH 'TEST-'
            DETACH DELETE jc
            """
        )

    _cleanup()
    ingest_job_code_spine(FIXTURE_XLSX, driver)
    yield
    _cleanup()


def test_overlay_applies_risk_edges_against_existing_spine(driver, spine):
    report = apply_mos_risk_overlay(FIXTURE_VALID, driver)

    assert report.entries_seen == 2
    assert report.entries_applied == 2
    # 11B: 2 anatomy + 1 symptom = 3; 2A5X1: 2 anatomy + 1 symptom = 3.
    assert report.risk_edges_written == 6

    anatomy = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: 'TEST-11B', branch: 'Army'})
              -[r:RISK_FOR]->(a:CFR:Anatomy)
        RETURN a.name AS name, r.confidence AS conf
        ORDER BY a.name
        """
    )
    assert anatomy == [
        {"name": "back", "conf": "medium"},
        {"name": "knee", "conf": "medium"},
    ]

    symptoms = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: 'TEST-11B', branch: 'Army'})
              -[r:RISK_FOR]->(s:CFR:Symptom)
        RETURN s.name AS name, r.source AS source
        """
    )
    assert symptoms == [
        {"name": "chronic-back-pain", "source": "duty-description-inference"}
    ]


def test_overlay_refuses_to_invent_job_codes(driver, spine):
    with pytest.raises(OverlayError, match="TEST-NOT-A-REAL-CODE"):
        apply_mos_risk_overlay(FIXTURE_INVALID_REF, driver)


def test_overlay_is_idempotent(driver, spine):
    r1 = apply_mos_risk_overlay(FIXTURE_VALID, driver)
    r2 = apply_mos_risk_overlay(FIXTURE_VALID, driver)
    assert r1.risk_edges_written == r2.risk_edges_written

    edge_counts = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: 'TEST-11B'})-[r:RISK_FOR]->(t)
        RETURN count(r) AS n
        """
    )
    # Re-running should not duplicate edges.
    assert edge_counts[0]["n"] == 3
