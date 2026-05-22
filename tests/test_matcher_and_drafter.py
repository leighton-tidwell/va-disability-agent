"""End-to-end tracer test for slice #4.

Setup:
- Insert a minimal DC 5260 graph state (skip live LLM extraction; we want a
  hermetic test of the matcher + drafter, not the extractor).
- Embed the Criterion nodes using a fake embedding provider that returns
  deterministic vectors keyed off body parts.
- Insert one persona's reports.
- Run find_candidate_dcs → assert DC 5260 is the top candidate.
- Run draft_lay_statement with a fake drafter that echoes report content.
- Assert the draft passes factuality.
"""

from __future__ import annotations

import hashlib

import pytest
from neo4j.exceptions import ServiceUnavailable

from va_agent.embeddings import (
    CRITERION_VECTOR_INDEX,
    EMBEDDING_DIM,
    EmbeddingProvider,
    ensure_criterion_vector_index,
)
from va_agent.graph.driver import GraphDriver
from va_agent.graph.tools import (
    record_measurement,
    record_symptom,
    record_veteran,
    reset_user,
)
from va_agent.output.drafter import DrafterLLM, draft_lay_statement
from va_agent.retrieval.matcher import find_candidate_dcs


def _stable_vec(seed: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic pseudo-embedding so we don't need real OpenAI calls."""
    h = hashlib.sha256(seed.lower().encode("utf-8")).digest()
    rng = list(h)
    vec = []
    while len(vec) < dim:
        for b in rng:
            vec.append((b - 128) / 128.0)
            if len(vec) >= dim:
                break
    # Normalise for cosine similarity sanity.
    import math

    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class _FakeEmbedder(EmbeddingProvider):
    """Returns the same vector for any text containing 'knee' or 'flexion'.

    Anchored on 'knee' so the matcher associates vet reports with knee criteria.
    """

    KNEE_VEC = _stable_vec("knee-anchor")
    OTHER_VEC = _stable_vec("other-anchor")

    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            if "knee" in tl or "flexion" in tl:
                out.append(self.KNEE_VEC)
            else:
                out.append(self.OTHER_VEC)
        return out


class _EchoDrafter(DrafterLLM):
    """Drafter that paraphrases the reports without inventing values.

    Walks the user message to find a 'flexion' value and a body part, then
    composes a one-sentence Lay Statement. This is *not* what production
    looks like — it's just enough to exercise the drafter wiring + factuality
    check without OpenAI calls.
    """

    def __init__(self) -> None:
        self.system_calls: list[str] = []
        self.user_calls: list[str] = []

    def draft(self, *, system: str, user: str) -> str:
        self.system_calls.append(system)
        self.user_calls.append(user)
        flexion_value = None
        functional_losses: list[str] = []
        flareup_severity = None
        for line in user.splitlines():
            if "flexion" in line.lower() and "=" in line:
                # Lines look like: "- flexion (knee) = 40 degrees"
                try:
                    rhs = line.rsplit("=", 1)[1].strip()
                    val_str = rhs.split()[0]
                    flexion_value = float(val_str)
                except (ValueError, IndexError):
                    continue
            if "functional_loss=" in line:
                losses = line.split("functional_loss=", 1)[1]
                functional_losses = [s.strip() for s in losses.split(",") if s.strip()]
            if "flare-up=" in line:
                flareup_severity = line.split("flare-up=", 1)[1].split()[0]
        parts = []
        if flexion_value is not None:
            parts.append(f"My knee flexion is limited to {flexion_value:g} degrees")
        if flareup_severity:
            parts.append(f"on bad days my symptoms are {flareup_severity}")
        if functional_losses:
            parts.append("functional losses include " + ", ".join(functional_losses))
        if not parts:
            return "I have ongoing knee pain."
        return ". ".join(parts).capitalize() + "."


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
def populated_graph(driver):
    """Insert a minimal DC 5260 with three Rating Levels + embeddings."""
    code = "5260"
    # Cleanup first
    queries = [
        "MATCH (m:CFR:Measurement)<-[:HAS_MEASUREMENT]-(c:CFR:Criterion)-[:CRITERION_FOR]->(dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE m",
        "MATCH (c:CFR:Criterion)-[:CRITERION_FOR]->(dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE c",
        "MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc",
    ]
    for q in queries:
        driver.cfr_write(q, code=code)

    # Seed DC + RatingLevels + Criteria + Measurements
    driver.cfr_write(
        """
        MERGE (s:CFR:Section {id: '4.71a'})
        MERGE (dc:CFR:DiagnosticCode {code: $code})
          SET dc.title = 'Leg, limitation of flexion of',
              dc.body_system = 'musculoskeletal',
              dc.raw_text = '',
              dc.retrieved_at = '2025-01-01'
        MERGE (dc)-[:IN_SECTION]->(s)
        """,
        code=code,
    )
    levels = [
        (10, "Flexion limited to 45°", 45),
        (20, "Flexion limited to 30°", 30),
        (30, "Flexion limited to 15°", 15),
    ]
    for percent, text, threshold in levels:
        driver.cfr_write(
            """
            MATCH (dc:CFR:DiagnosticCode {code: $code})
            MERGE (rl:CFR:RatingLevel {percent: $percent})
            MERGE (dc)-[:HAS_RATING]->(rl)
            MERGE (c:CFR:Criterion {text: $text})
              SET c.embedding = $emb
            MERGE (rl)-[:REQUIRES]->(c)
            MERGE (c)-[:CRITERION_FOR]->(dc)
            MERGE (m:CFR:Measurement {
                name: 'flexion', body_part: 'knee', operator: '<=', value: $thresh, unit: 'degrees'
            })
            MERGE (c)-[:HAS_MEASUREMENT]->(m)
            """,
            code=code,
            percent=percent,
            text=text,
            thresh=float(threshold),
            emb=_stable_vec("knee-anchor"),
        )

    ensure_criterion_vector_index(driver)

    yield

    for q in queries:
        driver.cfr_write(q, code=code)


@pytest.fixture
def persona(driver):
    uid = "test-tracer-persona"
    reset_user(driver, uid)
    record_veteran(driver, uid, branch="Army", discharge_characterization="Honorable")
    record_symptom(
        driver,
        uid,
        text="my knee bends only partway and locks up when I crouch",
        body_part="knee",
        typical_severity="moderate",
        flareup_severity="severe",
        flareup_frequency="weekly",
        functional_loss=["cannot kneel", "cannot run more than half a mile"],
    )
    record_measurement(
        driver, uid, name="flexion", body_part="knee", value=40, unit="degrees"
    )
    yield uid
    reset_user(driver, uid)


def test_matcher_finds_dc_5260(driver, populated_graph, persona):
    candidates = find_candidate_dcs(
        driver, persona, embedder=_FakeEmbedder(), top_k_per_report=5
    )
    assert candidates, "no candidates returned"
    top = candidates[0]
    assert top.code == "5260"
    # Veteran measured 40°, which satisfies the 45° threshold (10% level) but
    # not 30° or 15° — best supported percent is 10%.
    supported = {m["supports_percent"] for m in top.matching_measurements}
    assert 10 in supported
    assert 20 not in supported
    assert 30 not in supported
    assert top.best_percent >= 10


def test_drafter_produces_factually_grounded_text(driver, populated_graph, persona):
    drafter = _EchoDrafter()
    draft = draft_lay_statement(driver, persona, "5260", drafter=drafter, max_attempts=1)
    assert "40" in draft.text
    assert "knee" in draft.text.lower()
    assert draft.factuality.ok, (draft.text, draft.factuality.fabricated_facts)
    assert draft.attempts == 1


def test_drafter_retries_on_fabrication(driver, populated_graph, persona):
    class _BadThenGoodDrafter(DrafterLLM):
        def __init__(self) -> None:
            self.call = 0

        def draft(self, *, system: str, user: str) -> str:
            self.call += 1
            if self.call == 1:
                # Fabricate a value the veteran didn't report (15°).
                return "My knee flexion is limited to 15 degrees and I cannot kneel."
            return "My knee flexion is limited to 40 degrees and I cannot kneel."

    drafter = _BadThenGoodDrafter()
    draft = draft_lay_statement(driver, persona, "5260", drafter=drafter, max_attempts=2)
    assert draft.attempts == 2
    assert draft.factuality.ok
