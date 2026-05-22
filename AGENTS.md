# Agent Instructions

Instructions for AI agents (Claude Code, Cursor, Aider, etc.) working in this repo.

`CLAUDE.md` is a symlink to this file so Claude Code picks it up automatically.

## Project shape

Local-only Python learning project: knowledge graph + Graph RAG agent for VA disability claims. Stack: Python 3.12 (uv), Neo4j 5 in Docker, LangChain 1.x + LangGraph, Jupyter notebook as the primary dev surface. See [`docs/PRD.md`](docs/PRD.md) for the full v1 plan.

## Agent skills

### Backlog

Issues and PRDs live as GitHub issues at `leighton-tidwell/va-disability-agent`. Use the `gh` CLI. See `docs/agents/backlog.md`.

### Triage labels

Canonical five-role vocabulary, no overrides. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context. `CONTEXT.md` and `docs/adr/` are created lazily by `/grill-with-docs` as terms and decisions crystallise — don't flag their absence. See `docs/agents/domain.md`.
