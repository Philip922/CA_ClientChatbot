"""Embedding backends.

Two implementations behind one interface, because the two ends of the pipeline
have different constraints:

* `HostedEmbedder`  — an OpenAI-compatible `/embeddings` HTTP call. The only
  backend that fits a Lambda zip, so this is what serves queries in production.
* `LocalEmbedder`   — sentence-transformers. Offline index builds and tests.

Whichever builds the index must also serve the queries: vectors from different
models are not comparable. `VectorIndex` enforces that by storing the model name
and refusing a mismatch at load time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

import numpy as np

from config import Settings

# Hosted embedding endpoints cap the batch; 96 stays well inside every provider's
# limit while keeping the offline build to a handful of round trips.
_BATCH = 96


class EmbeddingError(RuntimeError):
    """Raised when a backend cannot produce vectors."""


@runtime_checkable
class Embedder(Protocol):
    """Encodes text into L2-normalised row vectors."""

    model_name: str

    async def encode(self, texts: list[str]) -> np.ndarray: ...


def normalise(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise rows so a dot product equals cosine similarity."""
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A zero vector would divide by zero; leave it at zero (scores 0 against all).
    np.divide(matrix, norms, out=matrix, where=norms > 0)
    return matrix


def _status_hint(exc, base_url: str) -> str:
    """Turn a provider HTTP error into something actionable.

    A raw `raise_for_status` traceback says which URL failed but not which of
    the handful of likely causes it was, and the caller sees it after paying for
    however many batches already succeeded.
    """
    status = exc.response.status_code
    if status in (401, 403):
        return (
            f"{base_url} rejected the key (HTTP {status}). Check that "
            f"EMBEDDINGS_API_KEY is a valid key for that endpoint, that it has "
            f"credit, and that EMBEDDINGS_BASE_URL points at the provider the "
            f"key belongs to."
        )
    if status == 404:
        return (
            f"{base_url}/embeddings does not exist (HTTP 404). Check "
            f"EMBEDDINGS_BASE_URL — it should end at the API version, e.g. "
            f"https://api.openai.com/v1 — and that the provider serves "
            f"embeddings at all."
        )
    if status == 429:
        return f"{base_url} rate-limited the request (HTTP 429). Retry shortly."
    body = exc.response.text[:200]
    return f"{base_url} returned HTTP {status}: {body}"


class HostedEmbedder:
    """Calls an OpenAI-compatible `POST {base_url}/embeddings`."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        if not api_key:
            raise EmbeddingError(
                "EMBEDDINGS_API_KEY is not set — the hosted embedding backend "
                "cannot encode queries without it."
            )
        # `.env.example` ships `sk-...`; copying it unedited otherwise surfaces
        # as a 401 from the provider, several hundred chunks into a build.
        if api_key.endswith("..."):
            raise EmbeddingError(
                f"EMBEDDINGS_API_KEY is still the placeholder from .env.example "
                f"({api_key!r}). Put a real key in .env — an OpenRouter key "
                f"works if EMBEDDINGS_BASE_URL points at OpenRouter."
            )
        # An OpenRouter key aimed at another provider 401s with nothing to
        # suggest the key itself is fine and merely pointed somewhere else.
        if api_key.startswith("sk-or-") and "openrouter.ai" not in base_url:
            raise EmbeddingError(
                f"EMBEDDINGS_API_KEY is an OpenRouter key (sk-or-…) but "
                f"EMBEDDINGS_BASE_URL is {base_url!r}, which will reject it. "
                f"Either set EMBEDDINGS_BASE_URL=https://openrouter.ai/api/v1 "
                f"(with a provider-prefixed EMBEDDING_MODEL such as "
                f"'openai/text-embedding-3-small'), or supply a key issued by "
                f"{urlparse(base_url).netloc or base_url}."
            )
        self.model_name = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def encode(self, texts: list[str]) -> np.ndarray:
        import httpx

        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start in range(0, len(texts), _BATCH):
                batch = texts[start : start + _BATCH]
                try:
                    response = await client.post(
                        f"{self._base_url}/embeddings",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={"model": self.model_name, "input": batch},
                    )
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    raise EmbeddingError(_status_hint(exc, self._base_url)) from exc
                except httpx.HTTPError as exc:
                    raise EmbeddingError(
                        f"could not reach {self._base_url}/embeddings "
                        f"({exc.__class__.__name__}: {exc})"
                    ) from exc

                data = payload.get("data")
                if not isinstance(data, list) or len(data) != len(batch):
                    raise EmbeddingError(
                        f"embedding response returned {len(data or [])} vectors "
                        f"for {len(batch)} inputs"
                    )
                # Providers are not required to preserve input order.
                for item in sorted(data, key=lambda entry: entry.get("index", 0)):
                    vectors.append(item["embedding"])

        return normalise(np.array(vectors, dtype=np.float32))


class LocalEmbedder:
    """sentence-transformers, loaded lazily so importing this module stays cheap."""

    def __init__(self, model: str):
        self.model_name = model
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - dev-only dependency
                raise EmbeddingError(
                    "EMBEDDING_BACKEND=local requires sentence-transformers "
                    "(pip install -r requirements-dev.txt). It is intentionally "
                    "absent from the Lambda package."
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    async def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        raw = self._load().encode(texts, convert_to_numpy=True)
        return normalise(raw)


def get_embedder(settings: Settings) -> Embedder:
    backend = settings.embedding_backend
    if backend == "local":
        return LocalEmbedder(settings.embedding_model)
    if backend == "hosted":
        return HostedEmbedder(
            base_url=settings.embeddings_base_url,
            api_key=settings.embeddings_api_key,
            model=settings.embedding_model,
            timeout=settings.request_timeout,
        )
    raise EmbeddingError(
        f"unknown EMBEDDING_BACKEND {backend!r} — expected 'hosted' or 'local'"
    )
