"""Thin wrapper around the neo4j driver that enforces query discipline.

Two rules the wrapper enforces:

1. **All queries are parameterised.** Cypher is never built by string concat
   with user input. Callers pass ``query`` + a params dict.
2. **User-side reads/writes must carry a ``user_id``.** The wrapper injects it
   into the params dict so an LLM-generated tool call can't omit or override
   it. The split between ``cfr_*`` and ``user_*`` methods makes the boundary
   visible in code.

For the v1 tracer the user-side methods are unused; they exist so slice #4
plugs in cleanly.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from neo4j import Driver, GraphDatabase

from ..config import neo4j_settings


class GraphDriver:
    def __init__(self, driver: Driver | None = None, database: str | None = None) -> None:
        if driver is None:
            settings = neo4j_settings()
            self._driver = GraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))
            self._database = database or settings.database
        else:
            self._driver = driver
            self._database = database or "neo4j"

    @classmethod
    def from_env(cls) -> "GraphDriver":
        return cls()

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "GraphDriver":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def session(self):
        with self._driver.session(database=self._database) as session:
            yield session

    # --- CFR-side operations ---------------------------------------------------

    def cfr_write(self, query: str, **params: Any) -> None:
        """Execute a write Cypher query against the CFR namespace.

        The query string MUST already scope to ``:CFR`` labels — the wrapper
        cannot enforce that without parsing Cypher. Callers should keep all
        write queries in ``va_agent.graph.writers`` so they're easy to audit.
        """
        with self.session() as session:
            session.run(query, **params)

    def cfr_read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    # --- User-side operations --------------------------------------------------

    def user_write(self, user_id: str, query: str, params: dict[str, Any] | None = None) -> None:
        """Execute a write Cypher query in the user namespace.

        ``user_id`` is forcibly bound into the query parameters. Any caller
        that supplies a conflicting ``user_id`` inside ``params`` triggers a
        ValueError — this is the guard against an LLM-generated tool call
        trying to escape its bound user scope.
        """
        merged = self._merge_user_params(user_id, params)
        with self.session() as session:
            session.run(query, **merged)

    def user_read(
        self, user_id: str, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        merged = self._merge_user_params(user_id, params)
        with self.session() as session:
            result = session.run(query, **merged)
            return [record.data() for record in result]

    @staticmethod
    def _merge_user_params(user_id: str, params: dict[str, Any] | None) -> dict[str, Any]:
        if not user_id:
            raise ValueError("user_id is required for user-side queries")
        merged = dict(params or {})
        if "user_id" in merged and merged["user_id"] != user_id:
            raise ValueError("user_id in params conflicts with bound user_id argument")
        merged["user_id"] = user_id
        return merged
