"""Smoke test for Slice 1: confirm we can connect to Neo4j, write, read, delete.

Skips automatically if Neo4j is not reachable, so CI without Docker doesn't fail.
"""

from __future__ import annotations

import pytest
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

from va_agent.config import neo4j_settings


@pytest.fixture(scope="module")
def driver():
    settings = neo4j_settings()
    try:
        d = GraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))
        d.verify_connectivity()
    except (ServiceUnavailable, OSError) as exc:
        pytest.skip(f"Neo4j not reachable at {settings.uri}: {exc}")
    yield d
    d.close()


def test_hello_graph_round_trip(driver):
    settings = neo4j_settings()
    test_code = "SMOKE-0001"
    with driver.session(database=settings.database) as session:
        session.run(
            "MERGE (d:CFR:DiagnosticCode {code: $code}) SET d.title = $title",
            code=test_code,
            title="Smoke test node",
        )
        result = session.run(
            "MATCH (d:DiagnosticCode {code: $code}) RETURN d.code AS code, d.title AS title",
            code=test_code,
        )
        record = result.single()
        assert record is not None
        assert record["code"] == test_code
        assert record["title"] == "Smoke test node"
        session.run("MATCH (d:DiagnosticCode {code: $code}) DETACH DELETE d", code=test_code)
