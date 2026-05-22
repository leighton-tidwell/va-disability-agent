"""Closing Output Generator tests.

Live-Neo4j integration test seeds a tiny graph (one DC + one Claim + one
ClaimedCondition + Lay Statement + Exam Prep + reports) and asserts the
expected Markdown files are written. Pure-Python tests cover the placeholder
substitution and template loading paths without Neo4j.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.graph.tools import (
    record_measurement,
    record_symptom,
    record_veteran,
    reset_user,
)
from va_agent.output.closing import (
    _filing_placeholders,
    _ConditionBundle,
    generate_session_output,
    render_template,
)
from va_agent.output.exam_prep import generate_exam_prep, persist_exam_prep, persist_lay_statement
from va_agent.review.claim_reviewer import create_claim

TEST_USER = "test-closing-user"
TEST_DC = "TESTCLOSING5260"


# --- Pure-Python tests (no Neo4j) -----------------------------------------


def test_render_template_substitutes_known_placeholders():
    tmpl = "Claim {{claim_id}} has {{n_conditions}} conditions."
    out = render_template(tmpl, {"claim_id": "abc", "n_conditions": "2"})
    assert out == "Claim abc has 2 conditions."


def test_render_template_leaves_unknown_placeholders_literal():
    tmpl = "Hello {{name}}, your code is {{unknown}}."
    out = render_template(tmpl, {"name": "Alice"})
    assert out == "Hello Alice, your code is {{unknown}}."


def test_filing_placeholders_includes_example_excerpt_from_first_lay_statement():
    bundle = _ConditionBundle(
        dc_code="5260",
        dc_title="Leg, limitation of flexion",
        body_system="musculoskeletal",
        best_percent=10,
        n_criteria=1,
        lay_statement={
            "text": "My right knee flexion is limited to 40 degrees on bad days. I cannot kneel.",
            "factuality_ok": True,
            "version": 1,
            "generated_at": "2026-01-01T00:00:00",
        },
        exam_prep=None,
    )
    p = _filing_placeholders("claim-xyz", [bundle])
    assert p["claim_id"] == "claim-xyz"
    assert p["n_conditions"] == "1"
    assert "DC 5260" in p["condition_list"]
    assert p["condition_title_example"] == "Leg, limitation of flexion"
    assert "40 degrees" in p["lay_statement_excerpt"]


def test_filing_placeholders_handles_empty_bundles():
    p = _filing_placeholders("claim-empty", [])
    assert p["n_conditions"] == "0"
    assert "_none_" in p["condition_list"]


# --- Live Neo4j integration test ------------------------------------------


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
def seeded(driver):
    # Clean any prior state.
    driver.cfr_write(
        "MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc",
        params={"code": TEST_DC},
    )
    driver.cfr_write(
        """
        MERGE (s:CFR:Section {id: 'TEST.4.71b'})
        MERGE (dc:CFR:DiagnosticCode {code: $code})
          SET dc.title = 'Test knee flexion DC', dc.body_system = 'musculoskeletal'
        MERGE (dc)-[:IN_SECTION]->(s)
        MERGE (rl:CFR:RatingLevel {percent: 10})
        MERGE (dc)-[:HAS_RATING]->(rl)
        MERGE (c:CFR:Criterion {text: 'Flexion limited to 45°'})
        MERGE (rl)-[:REQUIRES]->(c)
        MERGE (c)-[:CRITERION_FOR]->(dc)
        MERGE (a:CFR:Anatomy {name: 'test-closing-knee'}) SET a.body_system = 'musculoskeletal'
        MERGE (m:CFR:Measurement {
            name: 'flexion', body_part: 'test-closing-knee',
            operator: '<=', value: 45.0, unit: 'degrees'
        })
        MERGE (c)-[:HAS_MEASUREMENT]->(m)
        MERGE (m)-[:OF_ANATOMY]->(a)
        """,
        params={"code": TEST_DC},
    )

    reset_user(driver, TEST_USER)
    record_veteran(
        driver,
        TEST_USER,
        branch="Army",
        deployments=["Iraq 2008"],
        discharge_characterization="Honorable",
    )
    record_symptom(
        driver,
        TEST_USER,
        text="knee locks when crouching",
        body_part="test-closing-knee",
        typical_severity="moderate",
        flareup_severity="severe",
        flareup_frequency="weekly",
        flareup_duration="2 days",
        functional_loss=["cannot kneel"],
    )
    record_measurement(
        driver,
        TEST_USER,
        name="flexion",
        body_part="test-closing-knee",
        value=40,
        unit="degrees",
    )

    claim_id = create_claim(driver, TEST_USER, [TEST_DC])
    persist_lay_statement(
        driver,
        TEST_USER,
        claim_id=claim_id,
        dc_code=TEST_DC,
        text="My right knee flexion is limited to 40 degrees with severe flare-ups weekly.",
        factuality_ok=True,
    )
    prep = generate_exam_prep(driver, TEST_USER, TEST_DC)
    persist_exam_prep(driver, prep, claim_id=claim_id)

    yield claim_id

    reset_user(driver, TEST_USER)
    driver.cfr_write(
        "MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc",
        params={"code": TEST_DC},
    )
    driver.cfr_write(
        "MATCH (a:CFR:Anatomy {name: 'test-closing-knee'}) DETACH DELETE a"
    )
    driver.cfr_write("MATCH (s:CFR:Section {id: 'TEST.4.71b'}) DETACH DELETE s")


def test_generate_session_output_writes_expected_bundle(driver, seeded, tmp_path: Path):
    claim_id = seeded
    out_dir = generate_session_output(driver, TEST_USER, claim_id, tmp_path)

    assert out_dir == tmp_path / claim_id
    assert out_dir.exists()

    readme = (out_dir / "README.md").read_text()
    assert claim_id in readme
    assert "Army" in readme
    assert "Iraq 2008" in readme
    assert f"DC {TEST_DC}" in readme
    assert f"dc-{TEST_DC}.md" in readme

    condition = (out_dir / f"dc-{TEST_DC}.md").read_text()
    assert f"DC {TEST_DC}" in condition
    assert "Test knee flexion DC" in condition
    # Lay Statement text rendered verbatim.
    assert "40 degrees" in condition
    # Symptom report surfaced.
    assert "knee locks when crouching" in condition
    assert "cannot kneel" in condition
    # Best Rating Percentage surfaced (10% level matched by 40 <= 45).
    assert "10%" in condition
    # Evidence checklist present.
    assert "Service Treatment Record" in condition
    # Exam Prep surfaced.
    assert "examiner will measure" in condition.lower()
    # va.gov filing pointer.
    assert "va-gov-filing-steps.md" in condition

    filing = (out_dir / "va-gov-filing-steps.md").read_text()
    assert "https://www.va.gov/disability/file-disability-claim-form-21-526ez/" in filing
    assert "https://www.va.gov/sign-in/" in filing
    assert "https://www.va.gov/claim-or-appeal-status/" in filing
    assert claim_id in filing
    # Placeholder substitution actually happened.
    assert "{{claim_id}}" not in filing
    assert "{{n_conditions}}" not in filing
    assert "Test knee flexion DC" in filing
