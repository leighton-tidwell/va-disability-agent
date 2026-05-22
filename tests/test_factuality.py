"""Factuality check tests. Uses live Neo4j for the user-side graph."""

from __future__ import annotations

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.graph.tools import record_measurement, record_symptom, record_veteran, reset_user
from va_agent.retrieval.factuality import check_lay_statement


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
def user_id(driver):
    uid = "test-factuality-user"
    reset_user(driver, uid)
    record_veteran(driver, uid, branch="Army")
    record_symptom(
        driver, uid, text="knee pain on standing", body_part="knee", typical_severity="moderate"
    )
    record_measurement(
        driver, uid, name="flexion", body_part="knee", value=40, unit="degrees"
    )
    yield uid
    reset_user(driver, uid)


def test_grounded_draft_passes(driver, user_id):
    draft = (
        "My knee flexion is limited to 40 degrees and I experience pain when standing. "
        "I can no longer kneel without discomfort."
    )
    result = check_lay_statement(driver, user_id, draft)
    assert result.ok, (result.fabricated_facts, result.notes)
    assert any(f["value"] == 40 and f["unit"] == "degrees" for f in result.grounded_facts)


def test_fabricated_measurement_fails(driver, user_id):
    draft = "My knee flexion is limited to 15 degrees and I cannot kneel."
    result = check_lay_statement(driver, user_id, draft)
    assert not result.ok
    assert any(f["value"] == 15 for f in result.fabricated_facts)


def test_rounded_measurement_within_tolerance_passes(driver, user_id):
    draft = "My knee flexion is limited to approximately 42 degrees."
    result = check_lay_statement(driver, user_id, draft)
    assert result.ok, result.fabricated_facts


def test_unreported_body_part_warns(driver, user_id):
    # Veteran only reported knee — back is not in their reports.
    draft = "My knee bends to 40 degrees. My back also hurts frequently."
    result = check_lay_statement(driver, user_id, draft)
    # No fabricated measurements, so ok stays True, but a note is added.
    assert result.ok
    assert any("back" in n for n in result.notes)
