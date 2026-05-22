"""Tool functions used by the chat agent (Pattern 2: bound Cypher).

These functions are the only way the agent writes to the user-side subgraph.
``user_id`` is always bound at the server, never at the LLM. The LLM sees
named functions like ``record_symptom`` — not raw Cypher.

Every node carries:
- ``user_id`` for scoping
- ``recorded_at`` for ordering
- ``source`` for provenance ("user-stated", "DD-214", "medical-record", …)
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .driver import GraphDriver


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record_veteran(
    driver: GraphDriver,
    user_id: str,
    *,
    branch: str | None = None,
    service_period: dict[str, str] | None = None,
    deployments: list[str] | None = None,
    discharge_characterization: str | None = None,
) -> None:
    """Create or update the Veteran node for this user."""
    driver.user_write(
        user_id,
        """
        MERGE (v:User:Veteran {user_id: $user_id})
          SET v.branch = coalesce($branch, v.branch),
              v.discharge_characterization = coalesce($disc, v.discharge_characterization),
              v.recorded_at = coalesce(v.recorded_at, $now)
        """,
        params={
            "branch": branch,
            "disc": discharge_characterization,
            "now": _now_iso(),
        },
    )
    if service_period:
        driver.user_write(
            user_id,
            """
            MATCH (v:User:Veteran {user_id: $user_id})
            MERGE (sp:User:ServicePeriod {
                user_id: $user_id, start_date: $start, end_date: $end
            })
            MERGE (v)-[:HAS_SERVICE_PERIOD]->(sp)
            """,
            params={"start": service_period.get("start"), "end": service_period.get("end")},
        )
    for d in deployments or []:
        driver.user_write(
            user_id,
            """
            MATCH (v:User:Veteran {user_id: $user_id})
            MERGE (dep:User:Deployment {user_id: $user_id, name: $name})
            MERGE (v)-[:DEPLOYED_TO]->(dep)
            """,
            params={"name": d},
        )


def record_jobcode(driver: GraphDriver, user_id: str, *, code: str, branch: str) -> bool:
    """Link the veteran to their Job Code if it exists in the spine.

    Returns True if linked, False if the code wasn't in the spine.
    """
    rows = driver.user_read(
        user_id,
        """
        MATCH (jc:CFR:JobCode {code: $code, branch: $branch})
        RETURN jc.code AS code
        """,
        params={"code": code, "branch": branch},
    )
    if not rows:
        return False
    driver.user_write(
        user_id,
        """
        MATCH (v:User:Veteran {user_id: $user_id})
        MATCH (jc:CFR:JobCode {code: $code, branch: $branch})
        MERGE (v)-[:HOLDS_JOBCODE]->(jc)
        """,
        params={"code": code, "branch": branch},
    )
    return True


def record_symptom(
    driver: GraphDriver,
    user_id: str,
    *,
    text: str,
    body_part: str,
    typical_severity: str | None = None,
    flareup_severity: str | None = None,
    flareup_frequency: str | None = None,
    flareup_duration: str | None = None,
    functional_loss: list[str] | None = None,
    source: str = "user-stated",
) -> str:
    """Record a SymptomReport node. Returns its id."""
    report_id = str(uuid4())
    driver.user_write(
        user_id,
        """
        MERGE (v:User:Veteran {user_id: $user_id})
        MERGE (sr:User:SymptomReport {id: $id})
          SET sr.user_id = $user_id,
              sr.text = $text,
              sr.body_part = $body_part,
              sr.typical_severity = $typical_severity,
              sr.flareup_severity = $flareup_severity,
              sr.flareup_frequency = $flareup_frequency,
              sr.flareup_duration = $flareup_duration,
              sr.functional_loss = $functional_loss,
              sr.source = $source,
              sr.recorded_at = $now
        MERGE (v)-[:REPORTED]->(sr)
        WITH sr
        MERGE (a:CFR:Anatomy {name: $body_part})
        MERGE (sr)-[:LOCATED_IN]->(a)
        """,
        params={
            "id": report_id,
            "text": text,
            "body_part": body_part,
            "typical_severity": typical_severity,
            "flareup_severity": flareup_severity,
            "flareup_frequency": flareup_frequency,
            "flareup_duration": flareup_duration,
            "functional_loss": functional_loss or [],
            "source": source,
            "now": _now_iso(),
        },
    )
    return report_id


def record_measurement(
    driver: GraphDriver,
    user_id: str,
    *,
    name: str,
    body_part: str,
    value: float,
    unit: str,
    source: str = "user-stated",
) -> str:
    """Record a MeasurementReport node. Returns its id."""
    report_id = str(uuid4())
    driver.user_write(
        user_id,
        """
        MERGE (v:User:Veteran {user_id: $user_id})
        MERGE (mr:User:MeasurementReport {id: $id})
          SET mr.user_id = $user_id,
              mr.name = $name,
              mr.body_part = $body_part,
              mr.value = $value,
              mr.unit = $unit,
              mr.source = $source,
              mr.recorded_at = $now
        MERGE (v)-[:HAS_MEASUREMENT]->(mr)
        """,
        params={
            "id": report_id,
            "name": name,
            "body_part": body_part,
            "value": float(value),
            "unit": unit,
            "source": source,
            "now": _now_iso(),
        },
    )
    return report_id


def get_symptom_reports(driver: GraphDriver, user_id: str) -> list[dict]:
    return driver.user_read(
        user_id,
        """
        MATCH (v:User:Veteran {user_id: $user_id})-[:REPORTED]->(sr:User:SymptomReport)
        RETURN sr.id AS id, sr.text AS text, sr.body_part AS body_part,
               sr.typical_severity AS typical_severity,
               sr.flareup_severity AS flareup_severity,
               sr.flareup_frequency AS flareup_frequency,
               sr.flareup_duration AS flareup_duration,
               sr.functional_loss AS functional_loss
        ORDER BY sr.recorded_at
        """,
    )


def get_measurement_reports(driver: GraphDriver, user_id: str) -> list[dict]:
    return driver.user_read(
        user_id,
        """
        MATCH (v:User:Veteran {user_id: $user_id})-[:HAS_MEASUREMENT]->(mr:User:MeasurementReport)
        RETURN mr.id AS id, mr.name AS name, mr.body_part AS body_part,
               mr.value AS value, mr.unit AS unit
        ORDER BY mr.recorded_at
        """,
    )


def reset_user(driver: GraphDriver, user_id: str) -> None:
    """Delete everything in the user-side subgraph for this user. Test/dev only."""
    driver.user_write(
        user_id,
        """
        MATCH (n:User)
        WHERE n.user_id = $user_id
        DETACH DELETE n
        """,
    )
