"""Claim Reviewer tests.

Two fixture scenarios:

- A claim with two DCs that share an Anatomy → Pyramiding flagged.
- A claim with a one-sided paired Anatomy → Bilateral Factor prompt.

Live Neo4j is required; tests skip cleanly when it isn't reachable.
"""

from __future__ import annotations

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.graph.tools import record_symptom, record_veteran, reset_user
from va_agent.review.claim_reviewer import create_claim, review_claim


TEST_USER = "test-claim-reviewer-user"
TEST_DCS = ["TEST5260", "TEST5261", "TEST5262"]


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
    """Build a tiny law-side graph with two knee DCs (TEST5260 + TEST5261) that
    share the 'knee' Anatomy via OF_ANATOMY, plus a 'left-knee'/'right-knee'
    paired Anatomy pair for the Bilateral test (TEST5262 sits on left-knee)."""

    # Clean slate
    driver.cfr_write("MATCH (dc:CFR:DiagnosticCode) WHERE dc.code STARTS WITH 'TEST' DETACH DELETE dc")
    driver.cfr_write("MATCH (a:CFR:Anatomy) WHERE a.name STARTS WITH 'test-' DETACH DELETE a")

    # Anatomies
    for name in ("test-knee", "test-left-knee", "test-right-knee"):
        driver.cfr_write(
            "MERGE (a:CFR:Anatomy {name: $name}) SET a.body_system = 'musculoskeletal'",
            params={"name": name},
        )
    # Paired-with edge for laterality
    driver.cfr_write(
        """
        MATCH (l:CFR:Anatomy {name: 'test-left-knee'})
        MATCH (r:CFR:Anatomy {name: 'test-right-knee'})
        MERGE (l)-[:PAIRED_WITH]->(r)
        MERGE (r)-[:PAIRED_WITH]->(l)
        """
    )

    # Section + DCs
    driver.cfr_write("MERGE (s:CFR:Section {id: 'TEST.4.71a'})")
    for code, anatomy in (
        ("TEST5260", "test-knee"),
        ("TEST5261", "test-knee"),       # shares 'test-knee' with TEST5260 → pyramiding
        ("TEST5262", "test-left-knee"),  # one-sided → bilateral prompt
    ):
        driver.cfr_write(
            """
            MATCH (s:CFR:Section {id: 'TEST.4.71a'})
            MERGE (dc:CFR:DiagnosticCode {code: $code})
              SET dc.title = $code, dc.body_system = 'musculoskeletal'
            MERGE (dc)-[:IN_SECTION]->(s)
            MERGE (a:CFR:Anatomy {name: $anatomy})
            MERGE (dc)-[:RATES]->(a)
            MERGE (rl:CFR:RatingLevel {percent: 10})
            MERGE (dc)-[:HAS_RATING]->(rl)
            MERGE (c:CFR:Criterion {text: $code || ' criterion'})
            MERGE (rl)-[:REQUIRES]->(c)
            MERGE (c)-[:CRITERION_FOR]->(dc)
            MERGE (m:CFR:Measurement {
                name: 'flexion', body_part: $anatomy,
                operator: '<=', value: 45.0, unit: 'degrees'
            })
            MERGE (c)-[:HAS_MEASUREMENT]->(m)
            MERGE (m)-[:OF_ANATOMY]->(a)
            """,
            params={"code": code, "anatomy": anatomy},
        )

    reset_user(driver, TEST_USER)
    record_veteran(driver, TEST_USER, branch="Army")

    yield

    reset_user(driver, TEST_USER)
    driver.cfr_write("MATCH (dc:CFR:DiagnosticCode) WHERE dc.code STARTS WITH 'TEST' DETACH DELETE dc")
    driver.cfr_write("MATCH (a:CFR:Anatomy) WHERE a.name STARTS WITH 'test-' DETACH DELETE a")
    driver.cfr_write("MATCH (s:CFR:Section {id: 'TEST.4.71a'}) DETACH DELETE s")


def test_pyramiding_detected_when_two_dcs_share_anatomy(driver, fixture_graph):
    claim_id = create_claim(driver, TEST_USER, ["TEST5260", "TEST5261"])
    report = review_claim(driver, TEST_USER, claim_id)

    assert report.pyramiding, "pyramiding conflict not detected"
    conflict = report.pyramiding[0]
    assert set(conflict.dc_codes) == {"TEST5260", "TEST5261"}
    assert conflict.anatomy == "test-knee"
    assert "§4.14" in conflict.explanation

    # Persisted as a :User:Weakness
    rows = driver.user_read(
        TEST_USER,
        """
        MATCH (cl:User:Claim {id: $claim_id, user_id: $user_id})-[:HAS_WEAKNESS]->(w:User:Weakness)
        RETURN w.kind AS kind, w.dc_codes AS dc_codes, w.citation AS citation
        """,
        params={"claim_id": claim_id},
    )
    assert rows, "no Weakness node persisted"
    assert rows[0]["kind"] == "pyramiding"
    assert "§4.14" in rows[0]["citation"]


def test_bilateral_prompt_when_only_one_side_claimed(driver, fixture_graph):
    claim_id = create_claim(driver, TEST_USER, ["TEST5262"])
    report = review_claim(driver, TEST_USER, claim_id)

    assert report.bilateral_prompts, "bilateral prompt missing"
    prompt = report.bilateral_prompts[0]
    assert prompt.anatomy == "test-left-knee"
    assert prompt.paired_anatomy == "test-right-knee"
    assert "TEST5262" in prompt.dc_codes_on_claimed_side


def test_bilateral_prompt_suppressed_when_both_sides_have_reports(driver, fixture_graph):
    # Veteran reports on the paired side too.
    record_symptom(
        driver,
        TEST_USER,
        text="some right knee pain",
        body_part="test-right-knee",
        typical_severity="mild",
    )
    claim_id = create_claim(driver, TEST_USER, ["TEST5262"])
    report = review_claim(driver, TEST_USER, claim_id)
    # The bilateral check should NOT prompt for the same pair now.
    pair_targets = {(p.anatomy, p.paired_anatomy) for p in report.bilateral_prompts}
    assert ("test-left-knee", "test-right-knee") not in pair_targets


def test_missing_evidence_lists_canonical_types(driver, fixture_graph):
    claim_id = create_claim(driver, TEST_USER, ["TEST5260"])
    report = review_claim(driver, TEST_USER, claim_id)
    assert "TEST5260" in report.missing_evidence
    missing = report.missing_evidence["TEST5260"]
    # Lay Statement deliberately not in the gatherable list (slice #9 drafts it).
    assert "Service Treatment Record" in missing
    assert "Buddy Statement" in missing
    assert "Private Medical Record" in missing
    assert "Lay Statement" not in missing
