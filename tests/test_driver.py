"""Tests for the graph driver wrapper's user_id binding discipline.

Uses live Neo4j; skips if not reachable.
"""

from __future__ import annotations

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver


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


def test_user_write_requires_user_id(driver):
    with pytest.raises(ValueError, match="user_id is required"):
        driver.user_write("", "MERGE (v:User:Veteran {user_id: $user_id})")


def test_user_read_requires_user_id(driver):
    with pytest.raises(ValueError, match="user_id is required"):
        driver.user_read("", "MATCH (v:User:Veteran) RETURN v")


def test_user_write_binds_user_id(driver):
    user_id = "test-driver-binding"
    try:
        driver.user_write(
            user_id,
            "MERGE (v:User:Veteran {user_id: $user_id}) SET v.smoke = true",
        )
        rows = driver.user_read(
            user_id,
            "MATCH (v:User:Veteran {user_id: $user_id}) RETURN v.smoke AS smoke",
        )
        assert rows == [{"smoke": True}]
    finally:
        driver.user_write(
            user_id,
            "MATCH (v:User:Veteran {user_id: $user_id}) DETACH DELETE v",
        )


def test_user_id_param_conflict_raises(driver):
    # Simulates an LLM-generated tool call that tries to override the bound
    # user_id via the params dict.
    with pytest.raises(ValueError, match="conflicts"):
        driver.user_write(
            "me",
            "MERGE (v:User:Veteran {user_id: $user_id})",
            params={"user_id": "someone-else"},
        )
