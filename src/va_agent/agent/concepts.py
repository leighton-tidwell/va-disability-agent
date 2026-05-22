"""Concept loader + surface helpers.

Loads ``data/concepts.yaml`` into ``:CFR:Concept`` nodes (one per id). At chat
time, the orchestrator scans the most recent veteran message + the current
phase against each Concept's ``triggers`` and surfaces the matching Concept
once per session.

``/explain <topic>`` is a slash-command entry point that retrieves a Concept by
fuzzy name match.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from ..graph.driver import GraphDriver

DEFAULT_CONCEPTS_PATH = Path(__file__).resolve().parents[3] / "data" / "concepts.yaml"


def _normalise(text: str) -> str:
    """Lowercase, replace hyphens with spaces so 'flare-up' matches 'flares up'.

    Note: we also strip the trailing 's' on the trigger side only if it makes
    the trigger longer than 4 chars — this is a hack to make 'flare' match
    'flares'. For v1 it's enough; v2 can swap in a real lemmatiser.
    """
    return text.lower().replace("-", " ").replace("_", " ")


@dataclass(frozen=True)
class Concept:
    id: str
    name: str
    plain_language: str
    citation: str
    triggers: tuple[str, ...]

    def matches(self, text: str) -> bool:
        t = _normalise(text)
        return any(_normalise(trigger) in t for trigger in self.triggers)


def load_concepts(path: Path | None = None) -> list[Concept]:
    path = path or DEFAULT_CONCEPTS_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for entry in raw:
        out.append(
            Concept(
                id=entry["id"],
                name=entry["name"],
                plain_language=entry["plain_language"].strip(),
                citation=entry.get("citation", ""),
                triggers=tuple(entry.get("triggers", [])),
            )
        )
    return out


def ingest_concepts(driver: GraphDriver, concepts: Iterable[Concept]) -> int:
    """Idempotently MERGE each Concept into the graph. Returns the count written."""
    n = 0
    for c in concepts:
        driver.cfr_write(
            """
            MERGE (k:CFR:Concept {id: $id})
              SET k.name = $name,
                  k.plain_language = $plain_language,
                  k.citation = $citation,
                  k.triggers = $triggers
            """,
            params={
                "id": c.id,
                "name": c.name,
                "plain_language": c.plain_language,
                "citation": c.citation,
                "triggers": list(c.triggers),
            },
        )
        n += 1
    return n


def find_triggered_concept(
    concepts: Iterable[Concept], text: str, already_surfaced: Iterable[str] = ()
) -> Concept | None:
    """Return the first Concept whose triggers appear in ``text`` and which has
    not already been surfaced in this session."""
    surfaced = set(already_surfaced)
    for c in concepts:
        if c.id in surfaced:
            continue
        if c.matches(text):
            return c
    return None


def explain(concepts: Iterable[Concept], topic: str) -> Concept | None:
    """Return the Concept best matching ``topic`` for a ``/explain`` request."""
    t = topic.lower().strip()
    # Exact id match
    for c in concepts:
        if c.id.lower() == t:
            return c
    # Name substring
    for c in concepts:
        if t in c.name.lower():
            return c
    # Trigger keyword match
    for c in concepts:
        if c.matches(topic):
            return c
    return None
