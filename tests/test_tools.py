"""Tool function tests (Pattern 2): record_symptom, record_measurement, etc.

Uses live Neo4j; skips if unreachable.
"""

from __future__ import annotations

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.graph.tools import (
    get_measurement_reports,
    get_symptom_reports,
    record_measurement,
    record_symptom,
    record_veteran,
    reset_user,
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


@pytest.fixture
def user_id(driver):
    uid = "test-tools-user"
    reset_user(driver, uid)
    yield uid
    reset_user(driver, uid)


def test_record_veteran_creates_node(driver, user_id):
    record_veteran(driver, user_id, branch="Army", discharge_characterization="Honorable")
    rows = driver.user_read(
        user_id,
        "MATCH (v:User:Veteran {user_id: $user_id}) RETURN v.branch AS branch, v.discharge_characterization AS disc",
    )
    assert rows == [{"branch": "Army", "disc": "Honorable"}]


def test_record_symptom_creates_report_and_anatomy_link(driver, user_id):
    record_veteran(driver, user_id, branch="Army")
    sid = record_symptom(
        driver,
        user_id,
        text="knee locks up sometimes",
        body_part="knee",
        typical_severity="moderate",
        flareup_severity="severe",
        flareup_frequency="weekly",
        functional_loss=["cannot kneel", "cannot run"],
    )
    assert sid

    reports = get_symptom_reports(driver, user_id)
    assert len(reports) == 1
    r = reports[0]
    assert r["body_part"] == "knee"
    assert r["flareup_severity"] == "severe"
    assert sorted(r["functional_loss"]) == ["cannot kneel", "cannot run"]

    rows = driver.user_read(
        user_id,
        """
        MATCH (sr:User:SymptomReport {id: $id})-[:LOCATED_IN]->(a:CFR:Anatomy)
        RETURN a.name AS name
        """,
        params={"id": sid},
    )
    assert rows == [{"name": "knee"}]


def test_record_measurement(driver, user_id):
    record_veteran(driver, user_id, branch="Army")
    mid = record_measurement(
        driver, user_id, name="flexion", body_part="knee", value=40, unit="degrees"
    )
    assert mid
    reports = get_measurement_reports(driver, user_id)
    assert len(reports) == 1
    assert reports[0]["name"] == "flexion"
    assert reports[0]["value"] == 40.0
    assert reports[0]["unit"] == "degrees"


def test_reset_user_clears_all_user_nodes(driver, user_id):
    record_veteran(driver, user_id, branch="Army")
    record_symptom(driver, user_id, text="pain", body_part="knee")
    record_measurement(driver, user_id, name="flexion", body_part="knee", value=40, unit="degrees")

    reset_user(driver, user_id)

    rows = driver.user_read(
        user_id,
        "MATCH (n:User) WHERE n.user_id = $user_id RETURN count(n) AS c",
    )
    assert rows == [{"c": 0}]
