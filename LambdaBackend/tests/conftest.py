"""Shared fixtures.

Every test runs against a deterministic environment: `config.get_settings` is
cached per process, so anything that mutates the environment must clear it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402

BASE_ENV = {
    "OPENROUTER_API_KEY": "test-key",
    "MODEL_NAME": "anthropic/claude-sonnet-4-6",
    "CADRE_WEBSITE_URL": "https://cadreai.test",
    "SCRAPE_CACHE_TTL": "3600",
    "MAX_ITERATIONS": "3",
    "EMBEDDING_BACKEND": "hosted",
    "EMBEDDING_MODEL": "test-embed-model",
    "EMBEDDINGS_BASE_URL": "https://embeddings.test/v1",
    "EMBEDDINGS_API_KEY": "embed-key",
    "RAG_MIN_SCORE": "0.35",
    "RAG_TOP_K": "3",
    "BOOKING_LINK": "https://cadreai.com/book",
    "CORS_ALLOW_ORIGIN": "*",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_tool_caches():
    """Tools memoise the index and the scrape cache per container — reset both."""
    from orchestrator.tools import rag, scraper

    rag.reset_cache()
    scraper.reset_cache()
    yield
    rag.reset_cache()
    scraper.reset_cache()


class StubEmbedder:
    """Maps text to a vector by keyword, so similarity is predictable.

    Dimension i is 1 when `keywords[i]` appears in the text. Two texts sharing a
    keyword therefore score above zero and identical texts score 1.0.
    """

    def __init__(self, keywords: list[str], model_name: str = "test-embed-model"):
        self.keywords = keywords
        self.model_name = model_name
        self.calls: list[list[str]] = []

    async def encode(self, texts: list[str]) -> np.ndarray:
        from rag.embedder import normalise

        self.calls.append(texts)
        rows = [
            [1.0 if keyword in text.lower() else 0.0 for keyword in self.keywords]
            for text in texts
        ]
        return normalise(np.array(rows, dtype=np.float32))


@pytest.fixture
def embedder_factory():
    """Build a StubEmbedder over an arbitrary keyword set."""
    return StubEmbedder


@pytest.fixture
def stub_embedder() -> StubEmbedder:
    return StubEmbedder(["industries", "pricing", "strategy", "agents"])


@pytest.fixture
def sample_chunks() -> list[dict]:
    return [
        {
            "text": "Cadre AI serves industries including private equity and construction.",
            "source": "overview.pdf",
            "page": 1,
            "chunk_index": 0,
        },
        {
            "text": "Our AI strategy engagements produce a prioritised roadmap.",
            "source": "overview.pdf",
            "page": 2,
            "chunk_index": 0,
        },
        {
            "text": "We build autonomous agents for business processes.",
            "source": "services.pdf",
            "page": 1,
            "chunk_index": 0,
        },
    ]


@pytest.fixture
async def stub_index(sample_chunks, stub_embedder):
    from rag.index import build_index

    return await build_index(sample_chunks, stub_embedder)
