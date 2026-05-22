.PHONY: help up down restart logs notebook install sync test clean nuke install-skills

help:
	@echo "VA Disability Agent — make targets"
	@echo ""
	@echo "  make install         — sync Python dependencies via uv"
	@echo "  make install-skills  — install pinned agent skills"
	@echo "  make up              — start Neo4j via docker compose"
	@echo "  make down            — stop Neo4j"
	@echo "  make restart         — restart Neo4j"
	@echo "  make logs            — tail Neo4j logs"
	@echo "  make notebook        — launch JupyterLab"
	@echo "  make test            — run pytest"
	@echo "  make clean           — remove Python build artifacts"
	@echo "  make nuke            — stop Neo4j and wipe its data (destructive)"

install sync:
	uv sync --extra dev

install-skills:
	@bash scripts/install-skills.sh

up:
	docker compose up -d
	@echo "Waiting for Neo4j to be healthy..."
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' va-agent-neo4j 2>/dev/null)" = "healthy" ]; do sleep 2; done
	@echo "Neo4j Browser:  http://localhost:7474"
	@echo "Neo4j Bolt:     bolt://localhost:7687"

down:
	docker compose down

restart: down up

logs:
	docker compose logs -f neo4j

notebook:
	uv run --extra dev jupyter lab notebooks/

test:
	uv run --extra dev pytest

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

nuke: down
	@echo "Wiping Neo4j data..."
	rm -rf neo4j/data neo4j/logs neo4j/import neo4j/plugins
