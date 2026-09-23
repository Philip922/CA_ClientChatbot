"""`query_knowledge_base` — semantic search over the prebuilt PDF index."""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from config import get_settings
from orchestrator.prompts import tool_description
from rag.embedder import Embedder, get_embedder
from rag.index import VectorIndex

logger = logging.getLogger(__name__)

# Loaded once per Lambda container, on the first query rather than at import so
# a missing index cannot take down /health.
_index: VectorIndex | None = None
_embedder: Embedder | None = None


def _resources() -> tuple[VectorIndex, Embedder]:
    global _index, _embedder
    settings = get_settings()
    if _index is None:
        _index = VectorIndex.load(settings.index_path, expected_model=settings.embedding_model)
        logger.info(
            "loaded index: %d chunks, model=%s", len(_index), _index.model_name
        )
    if _embedder is None:
        _embedder = get_embedder(settings)
    return _index, _embedder


def reset_cache() -> None:
    """Drop the cached index and embedder. Used by tests."""
    global _index, _embedder
    _index = None
    _embedder = None


def _format_hits(hits: list[dict]) -> str:
    """Render hits for the LLM: scores inline so it can judge confidence."""
    lines = []
    for position, hit in enumerate(hits, start=1):
        lines.append(
            f"[{position}] {hit['source']} p.{hit['page']} (score {hit['score']:.2f})\n"
            f"{hit['chunk']}"
        )
    return "\n\n".join(lines)


@tool(
    "query_knowledge_base",
    description=tool_description("query_knowledge_base"),
    response_format="content_and_artifact",
)
async def query_knowledge_base(query: str, top_k: int = 0) -> tuple[str, list[dict]]:
    """Search the embedded PDF corpus. Returns matching chunks with scores."""
    settings = get_settings()
    limit = top_k if top_k and top_k > 0 else settings.rag_top_k

    try:
        index, embedder = _resources()
        hits = await index.search(
            query, embedder, top_k=limit, min_score=settings.rag_min_score
        )
    except Exception as exc:  # noqa: BLE001 - see below
        # Every failure is surfaced as content rather than raised: the prompt
        # tells the model to scrape when the knowledge base is down, and a raise
        # would instead cost a tool_end event and leave the UI's tool indicator
        # spinning. A corrupt index file, for instance, raises ValueError from
        # numpy — not something a narrower clause would catch.
        logger.warning("knowledge base query failed: %s", exc)
        return (
            f"The knowledge base is unavailable ({exc}). Try scrape_cadre_website "
            f"instead, or escalate.",
            [],
        )

    if not hits:
        return (
            f"No chunks scored above the {settings.rag_min_score} relevance "
            f"threshold for this query. The knowledge base does not cover it.",
            [],
        )

    payload = [hit.as_dict() for hit in hits]
    sources = [
        {
            "type": "document",
            "label": f"{hit['source']} · p.{hit['page']}",
            "excerpt": hit["chunk"],
        }
        for hit in payload
    ]
    return _format_hits(payload), sources
