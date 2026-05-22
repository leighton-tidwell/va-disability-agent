"""Anatomy enrichment + bilateral pairing tests against live Neo4j.

Uses the existing cleanup-fixture pattern and a small YAML fixture so the test
is hermetic w.r.t. the production ``data/anatomy.yaml`` registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.graph.driver import GraphDriver
from va_agent.graph.writers import apply_anatomy_metadata, load_anatomy_registry

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "anatomy_small.yaml"


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
def cleanup_knees(driver):
    names = ["knee", "left knee", "right knee"]
    for name in names:
        driver.cfr_write("MATCH (a:CFR:Anatomy {name: $name}) DETACH DELETE a", name=name)
    yield
    for name in names:
        driver.cfr_write("MATCH (a:CFR:Anatomy {name: $name}) DETACH DELETE a", name=name)


def test_load_small_registry():
    registry = load_anatomy_registry(FIXTURE)
    assert "left knee" in registry.entries
    assert registry.entries["left knee"].side == "left"
    assert registry.entries["right knee"].side == "right"
    assert "knee" in registry.pairs


def test_apply_enriches_and_pairs(driver, cleanup_knees):
    # Seed bare Anatomy nodes the way the existing writer does.
    for name in ["left knee", "right knee", "knee"]:
        driver.cfr_write(
            "MERGE (a:CFR:Anatomy {name: $name})",
            name=name,
        )

    registry = load_anatomy_registry(FIXTURE)
    report = apply_anatomy_metadata(driver, registry=registry)
    assert report["enriched"] >= 3
    assert report["paired"] >= 1

    rows = driver.cfr_read(
        """
        MATCH (l:CFR:Anatomy {name: 'left knee'})
        MATCH (r:CFR:Anatomy {name: 'right knee'})
        RETURN l.side AS l_side, r.side AS r_side,
               exists((l)-[:PAIRED_WITH]->(r)) AS paired_lr,
               exists((r)-[:PAIRED_WITH]->(l)) AS paired_rl,
               l.parent_anatomy AS parent
        """,
    )
    assert rows == [
        {
            "l_side": "left",
            "r_side": "right",
            "paired_lr": True,
            "paired_rl": True,
            "parent": "knee",
        }
    ]


def test_unspecified_default_for_unknown(driver):
    # An Anatomy node not in the registry should still pick up side='unspecified'
    # so query code can rely on the property always being present.
    name = "test-unknown-bone"
    driver.cfr_write("MATCH (a:CFR:Anatomy {name: $name}) DETACH DELETE a", name=name)
    try:
        driver.cfr_write("MERGE (a:CFR:Anatomy {name: $name})", name=name)
        apply_anatomy_metadata(driver, registry=load_anatomy_registry(FIXTURE))
        rows = driver.cfr_read(
            "MATCH (a:CFR:Anatomy {name: $name}) RETURN a.side AS side",
            name=name,
        )
        assert rows == [{"side": "unspecified"}]
    finally:
        driver.cfr_write("MATCH (a:CFR:Anatomy {name: $name}) DETACH DELETE a", name=name)
