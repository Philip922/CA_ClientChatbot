"""Chunking, index persistence, search thresholds, and the hosted embedder."""

from __future__ import annotations

import numpy as np
import pytest
import respx
from httpx import Response

from rag.embedder import EmbeddingError, HostedEmbedder, get_embedder, normalise
from rag.index import IndexError_, VectorIndex, build_index
from rag.loader import load_documents, split_text


# --- chunking ---------------------------------------------------------------


def test_split_text_respects_size_and_overlap():
    text = " ".join(f"word{i:03d}" for i in range(200))
    chunks = split_text(text, size=100, overlap=20)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    # Overlap means the tail of one chunk reappears at the head of the next.
    assert chunks[0].split()[-1] in chunks[1]


def test_split_text_never_cuts_mid_word():
    chunks = split_text(" ".join(["alpha", "bravo", "charlie"] * 20), size=40, overlap=5)
    for chunk in chunks:
        for token in chunk.split():
            assert token in {"alpha", "bravo", "charlie"}


def test_split_text_hard_cuts_a_token_longer_than_the_window():
    chunks = split_text("x" * 250, size=100, overlap=10)
    assert len(chunks) >= 3
    assert all(chunk for chunk in chunks)


def test_split_text_handles_empty_and_whitespace():
    assert split_text("", size=100, overlap=10) == []
    assert split_text("   \n\t ", size=100, overlap=10) == []


def test_split_text_rejects_overlap_at_or_above_size():
    with pytest.raises(ValueError):
        split_text("some text", size=50, overlap=50)


def test_load_documents_requires_the_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_documents(tmp_path / "missing")


def test_load_documents_is_empty_without_pdfs(tmp_path):
    (tmp_path / "notes.txt").write_text("ignored")
    assert load_documents(tmp_path) == []


# --- normalisation ----------------------------------------------------------


def test_normalise_produces_unit_rows():
    result = normalise(np.array([[3.0, 4.0], [1.0, 0.0]]))
    assert np.allclose(np.linalg.norm(result, axis=1), 1.0)


def test_normalise_leaves_a_zero_vector_alone():
    result = normalise(np.array([[0.0, 0.0]]))
    assert np.allclose(result, 0.0)


# --- index ------------------------------------------------------------------


async def test_build_index_rejects_an_empty_corpus(stub_embedder):
    with pytest.raises(IndexError_):
        await build_index([], stub_embedder)


async def test_index_roundtrips_through_disk(stub_index, tmp_path):
    path = tmp_path / "index.npz"
    stub_index.save(path)

    reloaded = VectorIndex.load(path, expected_model="test-embed-model")

    assert len(reloaded) == len(stub_index)
    assert reloaded.model_name == stub_index.model_name
    assert np.allclose(reloaded.vectors, stub_index.vectors)
    assert reloaded.chunks == stub_index.chunks


async def test_index_load_rejects_a_model_mismatch(stub_index, tmp_path):
    path = tmp_path / "index.npz"
    stub_index.save(path)

    with pytest.raises(IndexError_, match="not comparable"):
        VectorIndex.load(path, expected_model="a-different-model")


def test_index_load_reports_a_missing_file(tmp_path):
    with pytest.raises(IndexError_, match="build_index"):
        VectorIndex.load(tmp_path / "nope.npz")


def test_index_rejects_mismatched_vector_and_chunk_counts():
    with pytest.raises(IndexError_, match="inconsistent"):
        VectorIndex(np.zeros((2, 4), dtype=np.float32), [{"text": "one"}], "m")


# --- search -----------------------------------------------------------------


async def test_search_returns_the_matching_chunk_first(stub_index, stub_embedder):
    hits = await stub_index.search("which industries", stub_embedder, top_k=3, min_score=0.1)

    assert hits
    assert hits[0].source == "overview.pdf"
    assert hits[0].page == 1
    assert "industries" in hits[0].chunk.lower()


async def test_search_filters_below_the_threshold(stub_index, stub_embedder):
    # No keyword overlap, so every score is 0 and the floor removes everything.
    assert await stub_index.search("unrelated question", stub_embedder, min_score=0.35) == []


async def test_search_honours_top_k(stub_index, embedder_factory):
    # A keyword every chunk contains, so all three are above the floor.
    embedder = embedder_factory(["cadre", "our", "we"])
    index = await build_index(stub_index.chunks, embedder)

    assert len(await index.search("we our cadre", embedder, top_k=2, min_score=0.0)) <= 2


async def test_search_is_empty_for_a_blank_query(stub_index, stub_embedder):
    assert await stub_index.search("   ", stub_embedder) == []


async def test_search_rejects_a_dimension_mismatch(stub_index, embedder_factory):
    with pytest.raises(IndexError_, match="dimensions"):
        await stub_index.search("x", embedder_factory(["a", "b"]), min_score=0.0)


async def test_search_hit_serialises_with_a_rounded_score(stub_index, stub_embedder):
    hits = await stub_index.search("industries", stub_embedder, min_score=0.1)
    payload = hits[0].as_dict()

    assert set(payload) == {"chunk", "source", "page", "score"}
    assert 0.0 <= payload["score"] <= 1.0


# --- hosted embedder --------------------------------------------------------


@respx.mock
async def test_hosted_embedder_normalises_and_orders_by_index():
    respx.post("https://embeddings.test/v1/embeddings").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 2.0]},
                    {"index": 0, "embedding": [3.0, 4.0]},
                ]
            },
        )
    )
    embedder = HostedEmbedder("https://embeddings.test/v1", "k", "test-embed-model")

    vectors = await embedder.encode(["first", "second"])

    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    # Provider returned them out of order; index decides, not position.
    assert np.allclose(vectors[0], [0.6, 0.8])


@respx.mock
async def test_hosted_embedder_rejects_a_truncated_response():
    respx.post("https://embeddings.test/v1/embeddings").mock(
        return_value=Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
    )
    embedder = HostedEmbedder("https://embeddings.test/v1", "k", "m")

    with pytest.raises(EmbeddingError, match="1 vectors for 2 inputs"):
        await embedder.encode(["a", "b"])


async def test_hosted_embedder_short_circuits_on_no_input():
    embedder = HostedEmbedder("https://embeddings.test/v1", "k", "m")
    assert (await embedder.encode([])).size == 0


def test_hosted_embedder_requires_an_api_key():
    with pytest.raises(EmbeddingError, match="EMBEDDINGS_API_KEY"):
        HostedEmbedder("https://embeddings.test/v1", "", "m")


def test_get_embedder_rejects_an_unknown_backend(monkeypatch):
    from config import get_settings

    monkeypatch.setenv("EMBEDDING_BACKEND", "magic")
    get_settings.cache_clear()

    with pytest.raises(EmbeddingError, match="unknown EMBEDDING_BACKEND"):
        get_embedder(get_settings())


def test_hosted_embedder_rejects_the_env_example_placeholder():
    with pytest.raises(EmbeddingError, match="placeholder"):
        HostedEmbedder("https://embeddings.test/v1", "sk-...", "m")


def test_an_openrouter_key_aimed_elsewhere_is_caught_up_front():
    # The key is valid — just pointed at a provider that will not honour it.
    with pytest.raises(EmbeddingError, match="is an OpenRouter key"):
        HostedEmbedder("https://api.openai.com/v1", "sk-or-v1-realkey", "m")


def test_an_openrouter_key_is_accepted_against_openrouter():
    embedder = HostedEmbedder(
        "https://openrouter.ai/api/v1", "sk-or-v1-realkey", "openai/text-embedding-3-small"
    )
    assert embedder.model_name == "openai/text-embedding-3-small"


@respx.mock
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "rejected the key"),
        (403, "rejected the key"),
        (404, "does not exist"),
        (429, "rate-limited"),
        (500, "returned HTTP 500"),
    ],
)
async def test_provider_errors_name_the_likely_cause(status, expected):
    respx.post("https://embeddings.test/v1/embeddings").mock(
        return_value=Response(status, text="upstream detail")
    )
    embedder = HostedEmbedder("https://embeddings.test/v1", "a-real-looking-key", "m")

    with pytest.raises(EmbeddingError, match=expected):
        await embedder.encode(["text"])


@respx.mock
async def test_a_connection_failure_names_the_endpoint():
    import httpx as _httpx

    respx.post("https://embeddings.test/v1/embeddings").mock(
        side_effect=_httpx.ConnectError("no route")
    )
    embedder = HostedEmbedder("https://embeddings.test/v1", "a-real-looking-key", "m")

    with pytest.raises(EmbeddingError, match="could not reach https://embeddings.test/v1"):
        await embedder.encode(["text"])


def test_the_embeddings_key_falls_back_to_the_openrouter_key(monkeypatch):
    from config import get_settings

    monkeypatch.delenv("EMBEDDINGS_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-shared")
    get_settings.cache_clear()

    assert get_settings().embeddings_api_key == "sk-or-v1-shared"


def test_the_fallback_does_not_cross_providers(monkeypatch):
    # The whole point: an OpenRouter key must never be handed to OpenAI.
    from config import get_settings

    monkeypatch.delenv("EMBEDDINGS_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-shared")
    get_settings.cache_clear()

    assert get_settings().embeddings_api_key == ""
