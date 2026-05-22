"""Centralised configuration: loads .env, exposes connection settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv_once() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file)


_load_dotenv_once()


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str
    user: str
    password: str
    database: str


def neo4j_settings() -> Neo4jSettings:
    return Neo4jSettings(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "devpassword"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )


def openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or None
