"""Environment-derived settings, resolved once per Lambda container.

Every tunable in `backend-plan.md` section 5 lands here. Nothing else in the
codebase reads `os.environ` directly, so a missing variable surfaces in one
place instead of at the point of use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent

try:  # Local development convenience; the layer is absent in Lambda.
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover - production path
    pass


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _str(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- LLM (OpenRouter) ---
    openrouter_api_key: str
    openrouter_base_url: str
    model_name: str
    llm_temperature: float
    request_timeout: float

    # --- Agent loop ---
    max_iterations: int
    booking_link: str

    # --- Scraper ---
    cadre_website_url: str
    scrape_cache_ttl: int
    scrape_max_chars: int

    # --- RAG ---
    embedding_backend: str
    embedding_model: str
    embeddings_base_url: str
    embeddings_api_key: str
    index_path: Path
    documents_dir: Path
    rag_min_score: float
    rag_top_k: int
    chunk_size: int
    chunk_overlap: int

    # --- HTTP ---
    cors_allow_origin: str


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached per container. Call `get_settings.cache_clear()` in tests."""
    openrouter_base = _str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    embeddings_base = _str("EMBEDDINGS_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    # Embeddings default to OpenRouter, which serves an OpenAI-compatible
    # /embeddings endpoint — so one key covers both by default. The fallback is
    # deliberately host-scoped: silently sending an OpenRouter key to OpenAI is
    # exactly the mistake this is meant to prevent, not commit.
    embeddings_key = _str("EMBEDDINGS_API_KEY")
    if not embeddings_key and _same_host(embeddings_base, openrouter_base):
        embeddings_key = _str("OPENROUTER_API_KEY")

    return Settings(
        openrouter_api_key=_str("OPENROUTER_API_KEY"),
        openrouter_base_url=openrouter_base,
        model_name=_str("MODEL_NAME", "anthropic/claude-sonnet-4-6"),
        llm_temperature=_float("LLM_TEMPERATURE", 0.2),
        request_timeout=_float("REQUEST_TIMEOUT", 30.0),
        max_iterations=_int("MAX_ITERATIONS", 3),
        booking_link=_str("BOOKING_LINK", "https://cadreai.com/book"),
        cadre_website_url=_str("CADRE_WEBSITE_URL", "https://cadreai.com").rstrip("/"),
        scrape_cache_ttl=_int("SCRAPE_CACHE_TTL", 3600),
        scrape_max_chars=_int("SCRAPE_MAX_CHARS", 4000),
        embedding_backend=_str("EMBEDDING_BACKEND", "hosted").lower(),
        embedding_model=_str("EMBEDDING_MODEL", "openai/text-embedding-3-small"),
        embeddings_base_url=embeddings_base,
        embeddings_api_key=embeddings_key,
        index_path=Path(_str("INDEX_PATH") or BASE_DIR / "rag_index.npz"),
        documents_dir=Path(_str("DOCUMENTS_DIR") or BASE_DIR / "documents"),
        rag_min_score=_float("RAG_MIN_SCORE", 0.35),
        rag_top_k=_int("RAG_TOP_K", 3),
        chunk_size=_int("CHUNK_SIZE", 500),
        chunk_overlap=_int("CHUNK_OVERLAP", 50),
        cors_allow_origin=_str("CORS_ALLOW_ORIGIN", "*"),
    )
