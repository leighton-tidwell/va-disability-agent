"""Exam Prep generator tests. Live Neo4j; skips when unreachable."""

from __future__ import annotations

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.graph.tools import record_measurement, record_symptom, record_veteran, reset_user
from va_agent.output.exam_prep import (
    ExamPrep,
    generate_exam_prep,
    persist_exam_prep,
    persist_lay_statement,
)
from va_agent.review.claim_reviewer import create_claim

TEST_USER = "test-exam-prep-user"
TEST_DC = "TEST9260"


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
def fixture_graph(driver):
    driver.cfr_write("MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc", params={"code": TEST_DC})
    driver.cfr_write(
        """
        MERGE (s:CFR:Section {id: 'TEST.4.71a'})
        MERGE (dc:CFR:DiagnosticCode {code: $code})
          SET dc.title = 'Test knee DC', dc.body_system = 'musculoskeletal'
        MERGE (dc)-[:IN_SECTION]->(s)
        MERGE (rl:CFR:RatingLevel {percent: 10})
        MERGE (dc)-[:HAS_RATING]->(rl)
        MERGE (c:CFR:Criterion {text: 'Flexion limited to 45°'})
        MERGE (rl)-[:REQUIRES]->(c)
        MERGE (c)-[:CRITERION_FOR]->(dc)
        MERGE (a:CFR:Anatomy {name: 'test-exam-knee'}) SET a.body_system = 'musculoskeletal'
        MERGE (m:CFR:Measurement {
            name: 'flexion', body_part: 'test-exam-knee',
            operator: '<=', value: 45.0, unit: 'degrees'
        })
        MERGE (c)-[:HAS_MEASUREMENT]->(m)
        MERGE (m)-[:OF_ANATOMY]->(a)
        """,
        params={"code": TEST_DC},
    )

    reset_user(driver, TEST_USER)
    record_veteran(driver, TEST_USER, branch="Army")
    record_symptom(
        driver,
        TEST_USER,
        text="knee locks up when crouching",
        body_part="test-exam-knee",
        typical_severity="moderate",
        flareup_severity="severe",
        flareup_frequency="weekly",
        flareup_duration="2-3 days",
        functional_loss=["cannot kneel", "cannot squat"],
    )
    record_measurement(
        driver, TEST_USER, name="flexion", body_part="test-exam-knee", value=40, unit="degrees"
    )

    yield

    reset_user(driver, TEST_USER)
    driver.cfr_write("MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc", params={"code": TEST_DC})
    driver.cfr_write("MATCH (a:CFR:Anatomy {name: 'test-exam-knee'}) DETACH DELETE a")
    driver.cfr_write("MATCH (s:CFR:Section {id: 'TEST.4.71a'}) DETACH DELETE s")


def test_generate_exam_prep_includes_measurements_and_flare_guidance(driver, fixture_graph):
    prep = generate_exam_prep(driver, TEST_USER, TEST_DC)
    assert isinstance(prep, ExamPrep)
    assert prep.dc_code == TEST_DC

    # The examiner will measure flexion
    assert any(m["name"] == "flexion" and m["body_part"] == "test-exam-knee" for m in prep.will_measure)
    # Describe-to-examiner block references the flare severity
    flare_lines = "\n".join(prep.describe_to_examiner)
    assert "severe" in flare_lines.lower()
    assert "weekly" in flare_lines.lower()
    # Functional Loss surfaced
    assert any("cannot kneel" in line for line in prep.describe_to_examiner)
    # Records-to-bring includes the veteran's stated measurement
    assert any("40" in r and "degrees" in r for r in prep.records_to_bring)


def test_persist_exam_prep_versions(driver, fixture_graph):
    claim_id = create_claim(driver, TEST_USER, [TEST_DC])
    prep = generate_exam_prep(driver, TEST_USER, TEST_DC)
    id1 = persist_exam_prep(driver, prep, claim_id=claim_id)
    id2 = persist_exam_prep(driver, prep, claim_id=claim_id)
    assert id1 != id2
    rows = driver.user_read(
        TEST_USER,
        "MATCH (ep:User:ExamPrep) WHERE ep.dc_code = $code RETURN ep.version AS v ORDER BY v",
        params={"code": TEST_DC},
    )
    versions = [r["v"] for r in rows]
    assert versions == [1, 2]


def test_persist_lay_statement_attaches_to_claimed_condition(driver, fixture_graph):
    claim_id = create_claim(driver, TEST_USER, [TEST_DC])
    persist_lay_statement(
        driver,
        TEST_USER,
        claim_id=claim_id,
        dc_code=TEST_DC,
        text="My knee flexion is limited to 40 degrees.",
        factuality_ok=True,
    )
    rows = driver.user_read(
        TEST_USER,
        """
        MATCH (cl:User:Claim {id: $claim_id, user_id: $user_id})-[:CLAIMS]->(cc:User:ClaimedCondition)
              -[:HAS_LAY_STATEMENT]->(ls:User:LayStatement)
        RETURN ls.text AS text, ls.factuality_ok AS ok, ls.version AS v
        """,
        params={"claim_id": claim_id},
    )
    assert rows
    assert "40 degrees" in rows[0]["text"]
    assert rows[0]["ok"] is True
    assert rows[0]["v"] == 1
