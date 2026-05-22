"""Live-Neo4j tests for the JobCode spine ingester.

All test fixtures use ``TEST-`` prefixed codes so they cannot collide with
either the real xlsx data or another concurrent agent's writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.ingestion.jobcodes import ingest_job_code_spine

FIXTURE = Path(__file__).parent / "fixtures" / "duty_mos_noise_fixture.xlsx"


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
def cleanup_test_jobcodes(driver):
    """Delete any TEST- prefixed JobCodes before and after each test."""

    def _cleanup():
        driver.cfr_write(
            """
            MATCH (jc:CFR:JobCode) WHERE jc.code STARTS WITH 'TEST-'
            DETACH DELETE jc
            """
        )

    _cleanup()
    yield
    _cleanup()


def test_ingest_fixture_xlsx(driver, cleanup_test_jobcodes):
    report = ingest_job_code_spine(FIXTURE, driver)

    # Fixture has 3 + 2 + 2 + 1 + 1 = 9 valid rows (plus one blank Army row).
    assert report.rows_written == 9, report
    assert set(report.per_branch.keys()) == {
        "Army",
        "Navy",
        "Air Force",
        "Marine Corps",
        "Coast Guard",
    }, report.per_branch
    assert report.per_branch["Army"] == 3

    rows = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: $code, branch: 'Army'})
              -[r:NOISE_EXPOSURE]->(a:CFR:Anatomy {name: 'hearing'})
        RETURN jc.title AS title, jc.type AS type, r.probability AS prob,
               a.body_system AS bs
        """,
        code="TEST-11B",
    )
    assert rows == [
        {
            "title": "INFANTRYMAN",
            "type": "MOS",
            "prob": "Highly Probable",
            "bs": "hearing",
        }
    ]

    # Probability normalisation across the three buckets.
    probs = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode)-[r:NOISE_EXPOSURE]->(:CFR:Anatomy {name: 'hearing'})
        WHERE jc.code STARTS WITH 'TEST-'
        RETURN DISTINCT r.probability AS p
        ORDER BY p
        """
    )
    assert {row["p"] for row in probs} == {"Highly Probable", "Moderate", "Low"}

    # Type inference for non-Army branches.
    af = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: 'TEST-2A5X1'}) RETURN jc.type AS type, jc.branch AS b
        """
    )
    assert af == [{"type": "AFSC", "b": "Air Force"}]

    navy = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: 'TEST-BM'}) RETURN jc.type AS type, jc.branch AS b
        """
    )
    assert navy == [{"type": "Navy Rating", "b": "Navy"}]

    marine = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode {code: 'TEST-03XX-E'})
        RETURN jc.type AS type, jc.branch AS b, jc.title AS title
        """
    )
    assert marine == [
        {"type": "MOS", "b": "Marine Corps", "title": "INFANTRY"}
    ]


def test_ingest_is_idempotent(driver, cleanup_test_jobcodes):
    r1 = ingest_job_code_spine(FIXTURE, driver)
    r2 = ingest_job_code_spine(FIXTURE, driver)
    assert r1.rows_written == r2.rows_written

    counts = driver.cfr_read(
        """
        MATCH (jc:CFR:JobCode)-[r:NOISE_EXPOSURE]->(:CFR:Anatomy {name: 'hearing'})
        WHERE jc.code STARTS WITH 'TEST-'
        RETURN count(jc) AS jc_count, count(r) AS edge_count
        """
    )
    # Same nodes, same edges — MERGE means re-running is a no-op.
    assert counts[0]["jc_count"] == 9
    assert counts[0]["edge_count"] == 9


@pytest.mark.slow
def test_ingest_real_xlsx_covers_all_branches(driver):
    """End-to-end ingestion of the committed Fast Letter 10-35 xlsx.

    Gated behind the ``slow`` marker. Writes to the real graph using real
    (non-prefixed) JobCode nodes — does not clean up.
    """
    project_root = Path(__file__).resolve().parents[1]
    xlsx = project_root / "data" / "duty_mos_noise.xlsx"
    report = ingest_job_code_spine(xlsx, driver)

    # Every branch the workbook covers must show up.
    expected = {"Army", "Navy", "Marine Corps", "Air Force", "Coast Guard"}
    assert expected.issubset(set(report.per_branch.keys())), report.per_branch
    assert report.rows_written > 1000
