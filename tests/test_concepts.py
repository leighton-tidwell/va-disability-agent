"""Concept loading + surfacing tests (pure-Python, no Neo4j)."""

from __future__ import annotations

from va_agent.agent.concepts import Concept, explain, find_triggered_concept, load_concepts


def test_load_concepts_returns_nonempty_list():
    concepts = load_concepts()
    assert len(concepts) >= 5
    by_id = {c.id: c for c in concepts}
    assert "worst-day-rule" in by_id
    assert "service-connection" in by_id
    assert "pyramiding" in by_id
    assert "bilateral-factor" in by_id


def test_concept_matches_by_trigger():
    c = Concept(
        id="t",
        name="Test",
        plain_language="...",
        citation="",
        triggers=("flare", "worst day"),
    )
    # Substring matches with case-insensitive normalisation
    assert c.matches("My knee really flares up on bad days")
    assert c.matches("It gets bad on my WORST day")
    # Hyphen-versus-space normalisation
    c2 = Concept(id="t2", name="T", plain_language="", citation="", triggers=("flare-up",))
    assert c2.matches("about a flare up every week")
    assert not c.matches("My knee hurts when I walk")


def test_find_triggered_concept_skips_already_surfaced():
    concepts = load_concepts()
    text = "my knee really flares up about twice a week"
    first = find_triggered_concept(concepts, text)
    assert first is not None
    assert first.id == "worst-day-rule"

    again = find_triggered_concept(concepts, text, already_surfaced=[first.id])
    assert again is None or again.id != first.id


def test_explain_matches_by_id_or_name():
    concepts = load_concepts()
    assert explain(concepts, "pyramiding").id == "pyramiding"
    assert explain(concepts, "worst-day-rule").id == "worst-day-rule"
    assert explain(concepts, "Bilateral Factor").id == "bilateral-factor"
    # trigger fallback
    hit = explain(concepts, "flare-up")
    assert hit is not None and hit.id == "worst-day-rule"


def test_explain_returns_none_for_unknown():
    concepts = load_concepts()
    assert explain(concepts, "quantum-mechanics") is None
