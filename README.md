# VA Disability Agent

A local knowledge-graph + Graph RAG agent that helps US veterans file accurate, well-rated VA disability claims.

The agent ingests **38 CFR Part 4** (the VA rating schedule) into a Neo4j knowledge graph, asks veterans the right questions based on their military job history and exposure, and produces copy-paste-ready claim narratives in CFR vocabulary — one per claimable condition.

**This is a learning project.** It is local-only, not deployed, not commercial. The primary goal is hands-on Graph RAG learning; the secondary (and load-bearing) goal is a tool the author would have wanted for his dad.

## Status

v1 in implementation. See [issue #1](https://github.com/leighton-tidwell/va-disability-agent/issues/1) for the PRD; implementation issues are tracked at [#2–#13](https://github.com/leighton-tidwell/va-disability-agent/issues). Domain glossary lives in [`CONTEXT.md`](CONTEXT.md).

## Stack

- Python 3.12 + `uv`
- Neo4j 5.x (Docker)
- LangChain 1.x + LangGraph
- Jupyter notebook as the primary dev surface

## Scope of v1

- 38 CFR §4.71a (musculoskeletal) + §4.1–§4.31 (general provisions)
- VA Duty MOS Noise Exposure Listing (all branches, all codes)
- Hand-curated musculoskeletal MOS risk overlay
- Hybrid vector + graph retrieval (Pattern 3 GraphRAG)
- LangGraph chat orchestrator that elicits symptoms, matches diagnostic codes, drafts CFR-language narratives
- Three vet personas as regression eval fixtures

## Not in v1

Deployment, multi-user, web UI, browser extension, other body systems, scraped advisory content. All on the v2+ roadmap.
