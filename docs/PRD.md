# VA Disability Agent — Product Requirements Document

**Status:** Draft v1 (2026-05-22)
**Author:** Leighton Tidwell (with Claude as collaborator)
**Project type:** Local-only learning project. Not commercial. Not deployed in v1.

---

## Problem Statement

A veteran sitting down to file a VA disability claim is fighting three things at once:

1. They don't know **what they're eligible for.** They were a crew chief 20 years ago; nobody told them their tinnitus, back pain, and knee stiffness are all separately claimable. Their friends got rated for things they never thought to mention.
2. They don't know **how to describe their symptoms** in a way the VA will reward. They write "my knee hurts when I walk" on the claim form. The rater can't map that to a diagnostic code. The vet gets a 0% rating or a denial. The same vet, asked the right questions, would have said "I can't fully straighten my knee, it locks up two or three times a week, and during flares I can't kneel at all" — which maps to multiple diagnostic codes and a substantially higher rating.
3. They don't know **what evidence to bring.** They show up to the C&P exam without recent medical records, without buddy letters, without the lay statements that would corroborate their symptoms.

The result, repeated across millions of veterans, is that vets are systematically under-rated — not because they're embellishing too little, but because they didn't know what to say or what to bring. The VA Code of Federal Regulations (38 CFR Part 4) is the rulebook, but it's 800+ pages of legal language no veteran will read.

**The author's father served 20 years ago and didn't know any of this. The author had to do it all for him. This app is the agent the author wishes he'd had then.**

## Solution

A local agent that:

1. **Knows 38 CFR Part 4 cold** by ingesting it into a knowledge graph, where every diagnostic code, rating criterion, measurement threshold, and cross-reference is a queryable, citeable fact.
2. **Asks the vet the right questions** based on their job history (MOS/AFSC/Rating), deployment, era, and reported issues — using a job-to-likely-conditions overlay grounded in the VA Duty MOS Noise Exposure Listing and curated musculoskeletal duty-description inferences.
3. **Translates plain-language reports into CFR vocabulary**, never inventing symptoms but aggressively eliciting them — including the "worst day, not best day" doctrine (§4.40/§4.45) that vets routinely under-apply to themselves.
4. **Produces, per claimable condition, a copy-paste paragraph** the vet drops directly into the va.gov claim form, plus a list of missing evidence to gather and step-by-step filing instructions.

The secondary, equally-weighted goal is **the author learning knowledge graphs and Graph RAG hands-on** in a way that transfers to his day job (`future-agents` repo). Every architecture decision balances "is this useful" against "is this a good learning artifact." When they conflict, learning wins.

## User Stories

### Veteran-facing (the dad test)

1. As a veteran, I want to start a session by telling the agent my branch, era, MOS/AFSC, and deployments, so that the agent can prompt me about likely conditions without me having to know what they are.
2. As a veteran whose MOS appears in the Duty MOS Noise Exposure xlsx, I want the agent to automatically surface tinnitus and hearing loss as candidate claims, so that I don't miss the #1 and #3 most-claimed VA conditions.
3. As a veteran with an obscure or recent job code not in the xlsx, I want the agent to tell me it doesn't recognize the code and ask me to describe my duties instead, so that I'm not silently filtered out.
4. As a veteran, I want the agent to ask me about each body system relevant to my job — knees, back, shoulders, hearing, mental health — even if I didn't bring them up, so that I don't forget conditions I'd legitimately claim.
5. As a veteran reporting a symptom, I want the agent to ask me both how the symptom is on a typical day **and** how it presents on my worst days during flare-ups, so that my claim reflects the §4.40/§4.45 "worst day" standard rather than my average state.
6. As a veteran who doesn't know a measurement (e.g. exact degree of knee flexion), I want to say "I don't know" without breaking the conversation, so that I can still progress and surface the gap as a "get this measured at your C&P" item.
7. As a veteran, I want to claim multiple conditions in one session — six, ten, fifteen — and have each one tracked separately, so that I leave with a complete claim package.
8. As a veteran, I want each candidate diagnostic code I'm eligible for to come with the rating percentage it supports, what specifically in my reports supports it, and what's still missing, so that I understand the claim before I file it.
9. As a veteran, I want the final output to be one copy-pasteable paragraph per condition, written in CFR vocabulary using my own reported facts, so that I can drop it directly into the va.gov claim form's description field.
10. As a veteran, I want step-by-step instructions on how to file at va.gov — which URL, which form, which fields to paste into — so that the chat session ends with me actually filing, not with another to-do.
11. As a veteran, I want to pause the session and come back later with my reports intact, so that I can gather records between sessions.
12. As a veteran who has additional conditions later, I want to start a new session and have my prior reports available, so that I'm building a profile, not redoing intake.
13. As a veteran encountering a VA-specific concept I don't know (pyramiding, bilateral factor, secondary service connection), I want the agent to explain it inline the first time it's relevant, so that I learn the system as I go without reading a manual up front.
14. As a veteran, I want to use a `/explain <topic>` command to get a focused explanation when I'm curious, so that I can dig deeper without derailing the flow.
15. As a veteran, I want to see exactly which diagnostic codes and criteria are being matched against my reports, so that I trust the agent's recommendations.
16. As a veteran, I want the agent to never put words in my mouth — never claim symptoms or severities I haven't confirmed — so that I'm not at risk of misrepresentation at the C&P or beyond.
17. As a veteran whose reports conflict with each other or with known medical realities, I want the agent to surface the conflict as a "weakness to address" rather than hide it, so that I'm not blindsided at the C&P.

### Developer-facing (the author's learning goals)

18. As the developer, I want every architectural decision to be explainable to someone unfamiliar with knowledge graphs, so that I'm building understanding, not just shipping code.
19. As the developer, I want the v1 implementation to live primarily in a Jupyter notebook with prose explanations between cells, so that I can inspect intermediate graph states and Cypher results as I go.
20. As the developer, I want a `Makefile` with `make up` / `make down` / `make notebook` targets to bring Neo4j up/down and launch Jupyter, so that the dev environment is one command away.
21. As the developer, I want every ingested CFR section to result in nodes I can see in the Neo4j Browser at `localhost:7474`, so that I'm visually confirming the graph is what I expect.
22. As the developer, I want to run a deliberately-failing Text2Cypher demo cell once, so that I understand why production Graph RAG systems don't trust the LLM to write Cypher unconstrained.
23. As the developer, I want the retrieval pipeline to use hybrid vector + graph traversal (Pattern 3), so that I'm exercising both the embeddings and the graph in every query.
24. As the developer, I want the LLM extraction step from CFR XML to produce Pydantic-validated objects gated by deterministic invariants, so that I learn the "LLM as extractor, code as validator" pattern.
25. As the developer, I want three named vet personas as fixtures + one held-out validation persona + a "weird input" suite, so that I have a regression eval I can run on every change.
26. As the developer, I want the v1 schema to generalize to other body systems (§4.130 mental, §4.85 hearing) without breaking changes, so that v2 is an additive expansion, not a rewrite.
27. As the developer, I want the dependency stack to mirror the `future-agents` workout-gen agent (LangChain 1.x, LangGraph, `neo4j` 5+, OpenAI/Anthropic), so that the learning transfers directly to work.

### Data integrity and safety

28. As the developer, I want every clinical fact in a drafted claim narrative to trace via Cypher query to a specific `:SymptomReport` or `:MeasurementReport` confirmed by the veteran, so that a graph-backed factuality check can reject any draft containing fabricated content.
29. As the developer, I want every user-side query to be wrapped by a driver layer that forcibly binds `user_id`, so that LLM-generated queries cannot leak cross-user data even in a multi-user future.
30. As the developer, I want every node sourced from the CFR to carry a citation property (section, paragraph, retrieval date), so that the agent can cite authoritative regulations in every response.
31. As the developer, I want a re-ingestion pipeline that diffs against prior state and flags stale nodes, so that when the CFR is updated the graph can be brought current intentionally rather than silently overwritten.

## Implementation Decisions

### Tech stack

- **Language:** Python 3.12, package management via `uv`.
- **Graph DB:** Neo4j 5.x, single instance via Docker Compose. Two databases not used; namespace via `:CFR` and `:User` labels.
- **Agent framework:** LangChain 1.x + LangGraph (matches `future-agents` workout-gen).
- **Driver:** Official `neo4j` Python driver, wrapped by a small custom layer that enforces `user_id` binding and parameterizes all queries.
- **LLM:** Provider-agnostic via LangChain; default to OpenAI for v1 (matches workout-gen). Anthropic is a drop-in alternative.
- **Embeddings:** OpenAI `text-embedding-3-small` (or equivalent) stored as `embedding` properties on text-bearing nodes.
- **CFR ingestion:** `lxml` for XML parsing, `pydantic` v2 for extraction schemas.
- **xlsx ingestion:** `openpyxl`.
- **Dev surface:** Jupyter notebook (`jupyterlab`), with prose explanations as Markdown cells between code cells.
- **Repo conventions:** `Makefile` lifecycle, `docker-compose.yml` for Neo4j, `pyproject.toml` for deps, `.env` for secrets (never committed).

### Knowledge sources

- **v1:** 38 CFR §4.71a + §4.1–§4.31 via the eCFR API (XML).
- **v1:** Duty MOS Noise Exposure Listing (VBA Fast Letter 10-35), public domain xlsx, all branches.
- **v1:** Hand-curated `data/mos_risk.yaml` for musculoskeletal duty-description inferences, sources tagged, attached only to existing spine nodes.
- **v2:** veteransbenefitskb.com via Firecrawl, local-only, separately-provenanced `:Source {trust: "advisory"}` subgraph.
- **v2:** Additional body systems (§4.130 mental next, then §4.85 hearing).
- **v2:** M21-1 Ch. 9 chemical exposure scraping from the KnowVA HTML.
- **v3+:** Browser extension for va.gov filing assist.

### Graph schema (v1)

**Law-side nodes (namespace `:CFR`):**
- `Section { id, title, body_system, text, source_citation, retrieved_at }`
- `DiagnosticCode { code, title, body_system, text, embedding, source_citation }`
- `RatingLevel { percent }`
- `Criterion { text, embedding }`
- `Measurement { name, body_part, operator, value, unit }`
- `Symptom { name, body_part }`
- `Severity { label }`
- `Anatomy { name, body_system, parent_anatomy }`
- `Rule { id, name, text, embedding }`
- `Concept { id, plain_language, citation, embedding }` — inline education content
- `JobCode { code, title, branch, type }` — MOS/AFSC/Rating/NEC

**Law-side relationships:**
- `(:DiagnosticCode)-[:IN_SECTION]->(:Section)`
- `(:DiagnosticCode)-[:RATES]->(:Anatomy)`
- `(:DiagnosticCode)-[:HAS_RATING]->(:RatingLevel)`
- `(:RatingLevel)-[:REQUIRES]->(:Criterion)`
- `(:Criterion)-[:HAS_MEASUREMENT]->(:Measurement)`
- `(:Criterion)-[:HAS_SYMPTOM]->(:Symptom)`
- `(:Criterion)-[:HAS_SEVERITY]->(:Severity)`
- `(:Criterion)-[:CRITERION_FOR]->(:DiagnosticCode)` — per-DC criteria (§4.71a style)
- `(:Criterion)-[:CRITERION_FOR_LEVEL]->(:RatingLevel)-[:IN_SECTION]->(:Section)` — section-wide formulas (§4.130 style)
- `(:DiagnosticCode | :Section | :Rule)-[:CROSS_REFERENCES]->(:DiagnosticCode | :Section | :Rule)`
- `(:Rule)-[:APPLIES_TO]->(:DiagnosticCode | :Anatomy | :Section)`
- `(:JobCode)-[:NOISE_EXPOSURE { probability }]->(:Anatomy { name: "hearing" })` — authoritative
- `(:JobCode)-[:RISK_FOR { confidence, source }]->(:Anatomy | :Symptom)` — curated overlay

**User-side nodes (namespace `:User`, all carry `user_id`):**
- `Veteran { user_id, branch, service_period, deployments }`
- `SymptomReport { text, body_part, typical_severity, flareup_severity, flareup_frequency, flareup_duration, functional_loss, source, recorded_at }`
- `MeasurementReport { value, unit, source, recorded_at }`
- `Claim { status, created_at }`
- `Evidence { type, text, status }`
- `Weakness { text, reason }`
- `ClaimNarrative { text, version, generated_at }`

**User-side relationships:**
- `(:Veteran)-[:HOLDS_JOBCODE]->(:JobCode)`
- `(:Veteran)-[:REPORTED]->(:SymptomReport)`
- `(:SymptomReport)-[:OBSERVES]->(:Symptom)`
- `(:SymptomReport)-[:LOCATED_IN]->(:Anatomy)`
- `(:Veteran)-[:HAS_MEASUREMENT]->(:MeasurementReport)`
- `(:MeasurementReport)-[:OF_TYPE]->(:Measurement)`
- `(:Veteran)-[:HAS_DRAFT_CLAIM]->(:Claim)`
- `(:Claim)-[:CLAIMS]->(:DiagnosticCode)`
- `(:Claim)-[:HAS_NARRATIVE]->(:ClaimNarrative)`
- `(:Claim)-[:SUPPORTED_BY]->(:Evidence)`
- `(:Claim)-[:HAS_WEAKNESS]->(:Weakness)`

### Modules (deep modules where the interface stays simple and stable)

1. **CFR Ingestion** — Fetches eCFR XML for a section, parses, runs LLM extractor + Pydantic schema + deterministic validator, writes passing nodes to the graph and failures to a review queue. Interface: `ingest_section(section_id) -> IngestionReport`.
2. **JobCode Spine Ingestion** — Reads the Duty MOS Noise Exposure xlsx, creates `:JobCode` nodes and `:NOISE_EXPOSURE` edges. Interface: `ingest_job_code_spine(xlsx_path) -> IngestionReport`.
3. **MOS Risk Overlay** — Loads `data/mos_risk.yaml`, validates every referenced job code exists in the spine, attaches `:RISK_FOR` edges. Interface: `apply_mos_risk_overlay(yaml_path) -> OverlayReport`.
4. **Graph Driver Wrapper** — Wraps `neo4j` driver with required `user_id` binding for all user-side queries, parameterizes all input, exposes a small set of read tool functions to the agent. Interface: a set of named tool functions, never raw Cypher from the LLM.
5. **Vet Profile Builder** — Tool functions used by the chat agent during a session: `record_jobcode`, `record_symptom`, `record_measurement`, `record_evidence`, `record_weakness`. Each enforces `user_id` and writes to the user subgraph.
6. **Match Retriever** — Hybrid vector + graph retrieval. Given `user_id`, embeds the vet's reported symptoms/measurements, vector-searches `Criterion` nodes, then graph-traverses to DCs and RatingLevels, returning ranked candidates with supporting evidence per candidate. Interface: `find_candidate_dcs(user_id) -> list[CandidateDC]`.
7. **Claim Narrative Drafter** — For a `(user_id, dc_code)` pair, deterministically gathers the vet's relevant reports and the DC's criteria, prompts the LLM to write the CFR-vocabulary paragraph, then runs the Cypher-backed factuality check (every clinical fact in the draft must trace to a confirmed report). Interface: `draft_narrative(user_id, dc_code) -> NarrativeDraft`.
8. **Concept Surface** — Loads `data/concepts.yaml` into `:Concept` nodes; chat orchestrator surfaces relevant explanations inline; `/explain <topic>` retrieves and returns one. Interface: `get_concept(topic) -> Concept`.
9. **Chat Orchestrator (LangGraph)** — The state machine: Intake → JobProfile → SymptomExploration (loops) → MeasurementCheck → MatchCandidates → EvidenceReview → ClaimNarrativeDraft → Review/Edit. State persists in the graph itself, so sessions resume.
10. **Persona Eval Harness** — Loads persona YAMLs, simulates a chat session by feeding scripted vet inputs through the orchestrator, captures candidate DCs, drafted narratives, and any factuality failures; reports against expected outcomes.
11. **Closing Output Generator** — Per claimed condition, produces a Markdown bundle with the narrative, supporting reports, missing evidence, and templated va.gov filing instructions.

### Retrieval strategy

- **Pattern 3 (vector + graph)** is the default for matching vet reports to CFR criteria.
- **Pattern 2 (tool-bound Cypher)** is used for all writes (Vet Profile Builder) and for sensitive reads where determinism matters (Claim Narrative Drafter's evidence gathering).
- **Pattern 1 (Text2Cypher)** appears once as a deliberate-failure demo cell in the notebook; not used in the production agent path.

### Agent behavior guardrails

- The agent **may proactively ask** about symptoms common to the vet's job, era, and exposure, including flare-up severity per §4.40/§4.45.
- The agent **may rephrase** vet reports into CFR vocabulary as mechanical translation against a glossary.
- The agent **may not assert** symptoms, severities, or measurements the vet has not confirmed.
- Every clinical fact in the drafted narrative must trace to a confirmed report via Cypher; drafts that fail the trace check are rejected and regenerated.
- Conflicts and missing evidence are surfaced explicitly as `:Weakness` and "missing evidence" items, not hidden.

### Repo layout

```
va-disability-agent/
├── docs/
│   ├── PRD.md
│   └── architecture/                  # diagrams, schema visualization, etc.
├── notebooks/
│   ├── 00_setup.ipynb                 # Neo4j connection, deps, "hello graph"
│   ├── 01_ingest_cfr.ipynb            # CFR §4.71a + §4.1–4.31 ingestion
│   ├── 02_ingest_jobcodes.ipynb       # Duty MOS Noise xlsx + risk overlay
│   ├── 03_retrieval_demo.ipynb        # Pattern 3 walkthrough + Pattern 1 fail demo
│   ├── 04_agent_session.ipynb         # Full LangGraph chat against a persona
│   └── 05_persona_eval.ipynb          # Regression harness
├── src/va_agent/
│   ├── ingestion/
│   │   ├── cfr.py
│   │   ├── jobcodes.py
│   │   └── risk_overlay.py
│   ├── graph/
│   │   ├── driver.py                  # user_id-binding wrapper
│   │   ├── tools.py                   # Pattern 2 read/write tool functions
│   │   └── schema.py                  # node/edge type definitions, constraints
│   ├── retrieval/
│   │   ├── matcher.py                 # Pattern 3
│   │   └── factuality.py              # Cypher-backed trace check
│   ├── agent/
│   │   ├── graph_state.py             # LangGraph state shape
│   │   ├── nodes.py                   # LangGraph nodes (intake, symptom, …)
│   │   └── prompts.py
│   ├── output/
│   │   └── closing.py                 # Markdown bundle per condition
│   └── eval/
│       ├── personas.py
│       └── harness.py
├── data/
│   ├── ecfr_cache/                    # raw eCFR XML (cached, gitignored)
│   ├── duty_mos_noise.xlsx            # committed
│   ├── mos_risk.yaml                  # committed
│   ├── concepts.yaml                  # committed
│   ├── personas/
│   │   ├── training/                  # 3 personas
│   │   ├── validation/                # 1 held-out
│   │   └── weird/                     # 5 edge-case scripts
│   └── review_queue.jsonl             # failed extractions, gitignored
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── .env.example
├── .gitignore
└── README.md
```

## Testing Decisions

### What makes a good test in this project

- Tests assert **external behavior** of a module given a fixed graph state and inputs, not internal implementation details.
- Integration tests over LLM-bearing modules use **recorded LLM responses** (replay fixtures) so they're deterministic. The recording is itself a checked-in test artifact.
- The schema constraints (Neo4j unique constraints, required properties) are themselves part of the test surface — a malformed write fails at the DB layer before reaching test assertions.

### Modules slated for tests in v1

1. **CFR Ingestion validator** — pure Python, deterministic. Unit tests: given a sample extraction, assert validator pass/fail and the reason. Easy and high-value.
2. **JobCode Spine Ingestion** — given a tiny fixture xlsx with 10 rows, assert the right `:JobCode` nodes and `:NOISE_EXPOSURE` edges appear in a test Neo4j instance.
3. **MOS Risk Overlay** — given a fixture YAML referencing both valid and invalid job codes, assert valid ones produce edges and invalid ones raise.
4. **Graph Driver Wrapper** — assert that user-side queries without a `user_id` raise; assert that queries with one are scoped correctly.
5. **Match Retriever** — given a fixture graph (CFR + vet reports for one persona) and a vet user_id, assert the expected DC candidates appear in the ranked output. Replay LLM responses for any embedding calls.
6. **Claim Narrative Drafter factuality check** — the Cypher-backed trace check. Given a fixture draft and a fixture graph, assert that drafts with fabricated content fail the trace and drafts with grounded content pass.
7. **Persona Eval Harness** — itself tested with a tiny mock orchestrator that always returns a fixed script, asserting the harness records and compares outcomes correctly.

### Not tested in v1

- The Chat Orchestrator (LangGraph) end-to-end: tested via the Persona Eval Harness, not via unit tests. Persona eval is the right level for LLM-driven flow.
- The LLM extractor itself: too non-deterministic. Its **validator** is tested instead.
- Notebook code: notebooks are the dev surface, not the artifact. Modules they call are tested.

### Prior art

The closest reference is `future-agents/agents/workout-gen` — same Python + Neo4j + LangChain/LangGraph stack. Its testing patterns (LLM replay, fixture graphs, validator-as-test-surface) inform this PRD.

## Out of Scope

**Explicitly out of scope for v1:**

- Deployment of any kind (Vercel, Supabase, hosted Neo4j). v1 is local-only.
- Multi-user authentication. `user_id` is a property on user-side nodes, but only one user exists in v1 (`user_id = "me"`).
- A web UI. v1's interface is the Jupyter notebook chat cell.
- Browser extension for va.gov.
- Body systems other than musculoskeletal (§4.71a) and the general provisions (§4.1–§4.31).
- veteransbenefitskb.com ingestion (deferred to v2 as a separately-provenanced subgraph; the developer is aware the site's `robots.txt` opts out AI bots and the v2 ingestion is on the developer's own judgment as a local-only learner, with the explicit constraint that scraped content stays local and is never republished).
- M21-1 Ch. 9 chemical exposure mapping (v2).
- AFECD / DA PAM 611-21 / NAVPERS 18068 PDF parsing for non-noise hazard mapping (v2+).
- Real-vet feedback loop. v1 uses synthetic personas only; the limitation is explicitly acknowledged, not hidden.
- Filing automation. v1 produces copy-paste text and instructions; the vet does the filing.
- Temporal/lifecycle modeling for cancers and convalescent ratings (§4.114 etc).
- Hearing rating tables (§4.85 Table VI/VIa/VII) — schema is forward-compatible via a future `:RatingTable` node type.

## Further Notes

### On the learning-first framing

This project is justified by the author's need to learn knowledge graphs hands-on for application at his day job (`future-agents`). When design tradeoffs pit pedagogical clarity against shipping speed, pedagogical clarity wins. The Jupyter notebook is therefore not a "v1 hack to be replaced by a web app" — it is the v1 product. A web app, if it ever exists, is v3+.

### On scraping veteransbenefitskb.com

The site's `robots.txt` lists `ClaudeBot`, `GPTBot`, `anthropic-ai`, and ~25 other AI bots as opt-outs. Claude (the collaborator) raised this and recommended either (a) emailing the site authors for permission or (b) using the Reddit API to pull from r/VeteransBenefits directly. The developer, given v2's local-only learning scope and no commercial intent, elected to proceed via Firecrawl in v2 with the standing constraint that the scraped content remain local and never be republished. This note exists so the choice is visible, not hidden.

### On the eval loop's limits

Synthetic personas authored by the developer reflect the developer's assumptions about how vets describe themselves. The persona eval loop will tell us when changes break previously-passing flows; it will not tell us when the agent fails on a vet type we forgot to imagine. The "weird input" suite and the held-out validation persona are partial mitigations; the only complete mitigation is real-vet feedback, which is v3+.

### On the worst-day doctrine

§4.40 and §4.45 (and *DeLuca v. Brown*) require ratings to account for functional loss during flare-ups, weakness, fatigability, incoordination, and pain on use. Vets routinely under-report flares because the C&P exam captures their "normal" presentation. The agent eliciting flare-up severity is **applying the standard correctly**, not gaming it. This is modeled as first-class fields on `:SymptomReport` and surfaced as a `:Concept` the first time a vet describes a fluctuating symptom.

### On data freshness

eCFR content changes. The ingestion pipeline records `retrieved_at` and content hash on every node. A re-ingestion run diffs against the previous state and flags stale or removed nodes. The same discipline applies to the Duty MOS Noise xlsx (currently dated 2010 — newer cyber/space/drone MOSs may be absent).

### On AI assistance in this project

Claude was used extensively as a collaborator in the design phase (the conversation that produced this PRD), and will be used during implementation for code generation, extraction prompting, persona authoring, and self-improvement loop iteration. The author retains decision-making authority on all architectural and ethical questions; Claude's role is to surface tradeoffs, push back where warranted, and accelerate implementation.
