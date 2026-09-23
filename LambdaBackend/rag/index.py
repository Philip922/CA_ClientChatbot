"""In-memory vector index.

Vectors are L2-normalised at build time, so a single matrix-vector product is
the whole search: `vectors @ query` is cosine similarity for every chunk at
once. At the corpus sizes this PoC targets (thousands of chunks) that is
microseconds, and it keeps the runtime dependency list at numpy.

The index is built offline and shipped inside the zip as a `.npz`. Cold start is
a `np.load`, not a model load.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from rag.embedder import Embedder

_FORMAT_VERSION = 1


class IndexError_(RuntimeError):
    """Raised when an index is missing, malformed, or model-mismatched."""


@dataclass(frozen=True)
class SearchHit:
    chunk: str
    source: str
    page: int
    score: float

    def as_dict(self) -> dict:
        return {
            "chunk": self.chunk,
            "source": self.source,
            "page": self.page,
            "score": round(self.score, 4),
        }


class VectorIndex:
    def __init__(self, vectors: np.ndarray, chunks: list[dict], model_name: str):
        if len(vectors) != len(chunks):
            raise IndexError_(
                f"index is inconsistent: {len(vectors)} vectors, {len(chunks)} chunks"
            )
        self.vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        self.chunks = chunks
        self.model_name = model_name

    def __len__(self) -> int:
        return len(self.chunks)

    # --- persistence --------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write vectors plus a JSON metadata blob.

        Metadata travels as a JSON string rather than an object array so the
        index can be read back with `allow_pickle=False`.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "format_version": _FORMAT_VERSION,
            "model_name": self.model_name,
            "chunks": self.chunks,
        }
        np.savez_compressed(
            path, vectors=self.vectors, meta=np.array(json.dumps(meta), dtype=np.str_)
        )

    @classmethod
    def load(cls, path: Path, expected_model: str | None = None) -> VectorIndex:
        if not path.is_file():
            raise IndexError_(
                f"no index at {path} — run `python scripts/build_index.py` first"
            )
        with np.load(path, allow_pickle=False) as data:
            vectors = data["vectors"]
            meta = json.loads(str(data["meta"]))

        if meta.get("format_version") != _FORMAT_VERSION:
            raise IndexError_(
                f"index format {meta.get('format_version')} is not supported "
                f"(expected {_FORMAT_VERSION}) — rebuild the index"
            )

        model_name = meta["model_name"]
        if expected_model and model_name != expected_model:
            raise IndexError_(
                f"index was built with {model_name!r} but EMBEDDING_MODEL is "
                f"{expected_model!r}. Vectors from different models are not "
                f"comparable — rebuild the index or restore the variable."
            )
        return cls(vectors=vectors, chunks=meta["chunks"], model_name=model_name)

    # --- query --------------------------------------------------------------

    async def search(
        self,
        query: str,
        embedder: Embedder,
        top_k: int = 3,
        min_score: float = 0.35,
    ) -> list[SearchHit]:
        """Top `top_k` chunks scoring at or above `min_score`."""
        if not len(self) or not query.strip():
            return []

        encoded = await embedder.encode([query])
        vector = encoded[0]
        if vector.shape[0] != self.vectors.shape[1]:
            raise IndexError_(
                f"query vector has {vector.shape[0]} dimensions but the index "
                f"has {self.vectors.shape[1]} — the index was built with a "
                f"different embedding model"
            )

        scores = self.vectors @ vector
        # argpartition beats a full sort once the corpus outgrows a few hundred
        # chunks, and top_k is always tiny here.
        count = min(top_k, len(scores))
        candidates = np.argpartition(-scores, count - 1)[:count]
        ranked = candidates[np.argsort(-scores[candidates])]

        hits: list[SearchHit] = []
        for position in ranked:
            score = float(scores[position])
            if score < min_score:
                continue
            chunk = self.chunks[int(position)]
            hits.append(
                SearchHit(
                    chunk=chunk["text"],
                    source=chunk["source"],
                    page=int(chunk["page"]),
                    score=score,
                )
            )
        return hits


async def build_index(chunks: list[dict], embedder: Embedder) -> VectorIndex:
    """Embed `chunks` (as produced by `rag.loader`) into a fresh index."""
    if not chunks:
        raise IndexError_("no chunks to index — is documents/ empty?")
    vectors = await embedder.encode([chunk["text"] for chunk in chunks])
    return VectorIndex(vectors=vectors, chunks=chunks, model_name=embedder.model_name)
