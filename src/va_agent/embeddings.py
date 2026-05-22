"""Text → embedding via OpenAI, plus the Neo4j vector index for Criterion search."""

from __future__ import annotations

from typing import Protocol, Sequence

from langchain_openai import OpenAIEmbeddings

from .config import openai_api_key
from .graph.driver import GraphDriver

CRITERION_VECTOR_INDEX = "criterion_embedding"
EMBEDDING_DIM = 1536  # text-embedding-3-small default
EMBEDDING_MODEL = "text-embedding-3-small"


class EmbeddingProvider(Protocol):
    """Anything that turns a batch of strings into a batch of float vectors."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    def __init__(self, model: str = EMBEDDING_MODEL) -> None:
        kwargs: dict[str, object] = {"model": model}
        api_key = openai_api_key()
        if api_key:
            kwargs["api_key"] = api_key
        self._client = OpenAIEmbeddings(**kwargs)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._client.embed_documents(list(texts))


def ensure_criterion_vector_index(driver: GraphDriver) -> None:
    """Create the vector index over `:CFR:Criterion(embedding)` if missing.

    Idempotent — Neo4j returns silently if the index already exists.
    """
    driver.cfr_write(
        f"""
        CREATE VECTOR INDEX {CRITERION_VECTOR_INDEX} IF NOT EXISTS
        FOR (c:Criterion) ON (c.embedding)
        OPTIONS {{
          indexConfig: {{
            `vector.dimensions`: {EMBEDDING_DIM},
            `vector.similarity_function`: 'cosine'
          }}
        }}
        """
    )


def backfill_criterion_embeddings(
    driver: GraphDriver,
    provider: EmbeddingProvider | None = None,
    batch_size: int = 64,
) -> int:
    """Populate embeddings on Criterion nodes that don't yet have one.

    Returns the number of nodes embedded.
    """
    provider = provider or OpenAIEmbeddingProvider()
    rows = driver.cfr_read(
        "MATCH (c:CFR:Criterion) WHERE c.embedding IS NULL RETURN c.text AS text"
    )
    texts = [r["text"] for r in rows]
    if not texts:
        return 0

    total = 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors = provider.embed(batch)
        for text, vec in zip(batch, vectors, strict=True):
            driver.cfr_write(
                "MATCH (c:CFR:Criterion {text: $text}) SET c.embedding = $vec",
                text=text,
                vec=vec,
            )
            total += 1
    return total
