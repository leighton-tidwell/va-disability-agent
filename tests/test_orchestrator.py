"""End-to-end orchestrator test: drive a scripted persona through Intake →
JobProfile → SymptomExploration, assert the graph state and transcript.

Uses live Neo4j. We pre-seed a tiny JobCode spine entry so JobProfile's
spine lookup succeeds without depending on the full xlsx ingest.
"""

from __future__ import annotations

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.agent.concepts import load_concepts
from va_agent.agent.orchestrator import run_scripted_session
from va_agent.graph.driver import GraphDriver
from va_agent.graph.tools import reset_user

TEST_USER = "test-orchestrator-persona"
TEST_JOB_CODE = "15T"  # crew chief; used to assert hearing prioritisation


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
def seeded_graph(driver):
    """Insert a JobCode + risk overlay + Anatomy nodes so the orchestrator's
    spine lookup succeeds without depending on the full xlsx ingest."""
    driver.cfr_write("MATCH (n:CFR:Concept) DETACH DELETE n")
    # Concepts will be re-ingested by load_concepts → ingest_concepts elsewhere;
    # for this test we don't need them in the graph (the orchestrator surfaces
    # from the in-memory Concept list, not from the graph).

    # Anatomy nodes
    for a in ("hearing", "knee", "back", "shoulder"):
        driver.cfr_write(
            "MERGE (a:CFR:Anatomy {name: $name}) SET a.body_system = $bs",
            params={
                "name": a,
                "bs": "hearing" if a == "hearing" else "musculoskeletal",
            },
        )

    driver.cfr_write(
        """
        MERGE (jc:CFR:JobCode {code: $code, branch: $branch})
          SET jc.title = 'Test UH-60 Crew Chief', jc.type = 'MOS'
        WITH jc
        MATCH (a:CFR:Anatomy {name: 'hearing'})
        MERGE (jc)-[r:NOISE_EXPOSURE]->(a)
          SET r.probability = 'Highly Probable'
        WITH jc
        MATCH (b:CFR:Anatomy {name: 'back'})
        MERGE (jc)-[:RISK_FOR {source: 'duty-description-inference', confidence: 'medium'}]->(b)
        WITH jc
        MATCH (k:CFR:Anatomy {name: 'knee'})
        MERGE (jc)-[:RISK_FOR {source: 'duty-description-inference', confidence: 'medium'}]->(k)
        """,
        params={"code": TEST_JOB_CODE, "branch": "Army"},
    )

    reset_user(driver, TEST_USER)
    yield
    reset_user(driver, TEST_USER)
    driver.cfr_write(
        "MATCH (jc:CFR:JobCode {code: $code, branch: $branch}) DETACH DELETE jc",
        params={"code": TEST_JOB_CODE, "branch": "Army"},
    )


def test_orchestrator_runs_intake_through_first_anatomy(driver, seeded_graph):
    inputs = [
        # Intake: branch, deployments, discharge
        "I was in the Army from 2005 to 2014",
        "Iraq, twice",
        "Honorable",
        # JobProfile: job code
        f"My MOS was {TEST_JOB_CODE}",
        # First anatomy in queue is "hearing" (Highly Probable noise exposure):
        # yes / description / baseline / flare-up severity / freq / duration / probes
        "yes",
        "constant ringing in both ears and I miss words in conversations",
        "moderate",
        "severe",
        "daily",
        "all day",
        # 4 probes for "hearing":
        "no",  # cannot follow conversation in noisy restaurant (functional loss)
        "yes",  # don't repeat themselves to me (not a loss)
        "yes",  # I do have ringing (this maps to "yes — Do you have ringing...")
        "yes",  # TV not too loud (not a loss)
        # Next anatomy: "back" — say "no" to skip
        "no",
        # Next anatomy: "knee" — say "no" to skip
        "no",
    ]

    final = run_scripted_session(driver, TEST_USER, inputs, concepts=load_concepts())

    assert final["branch"] == "Army"
    assert final["discharge_characterization"] == "Honorable"
    assert final["job_code"] == TEST_JOB_CODE
    assert final["job_code_in_spine"] is True
    assert final["phase"] == "complete"
    assert "hearing" in final["prioritised_anatomies"]
    # Hearing should be prioritised first (Highly Probable noise exposure).
    assert final["prioritised_anatomies"][0] == "hearing"

    # One SymptomReport got persisted (for hearing).
    rows = driver.user_read(
        TEST_USER,
        "MATCH (v:User:Veteran {user_id: $user_id})-[:REPORTED]->(sr:User:SymptomReport) "
        "RETURN sr.body_part AS body_part, sr.typical_severity AS baseline, sr.flareup_severity AS flare",
    )
    assert any(r["body_part"] == "hearing" for r in rows)
    hearing_row = next(r for r in rows if r["body_part"] == "hearing")
    assert hearing_row["baseline"] == "moderate"
    assert hearing_row["flare"] == "severe"

    # The Worst-Day Rule concept should have surfaced once during the hearing flow.
    assert "worst-day-rule" in (final.get("surfaced_concepts") or [])


def test_orchestrator_warns_on_non_honorable_discharge(driver, seeded_graph):
    inputs = [
        "Army, 2010-2014",
        "none",
        "Other Than Honorable",
        f"MOS {TEST_JOB_CODE}",
        # End immediately — say no to first anatomy
        "no",
        # back, knee
        "no",
        "no",
    ]
    final = run_scripted_session(driver, TEST_USER, inputs, concepts=load_concepts())
    assert final["discharge_characterization"] == "Other Than Honorable"
    assert final["discharge_warning_issued"] is True
    # Warning text should be present in the transcript
    transcript_text = "\n".join(m["text"] for m in final["transcript"])
    assert "Character of Discharge" in transcript_text
