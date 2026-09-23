#!/usr/bin/env python3
"""Build the RAG index from the PDFs in `documents/`.

Run this locally whenever the corpus changes, then ship the resulting
`rag_index.npz` inside the Lambda zip. The deployed function never re-embeds
the corpus — it loads this file.

    python scripts/build_index.py                 # hosted embeddings (default)
    python scripts/build_index.py --backend local # sentence-transformers

The backend used here must match `EMBEDDING_BACKEND` / `EMBEDDING_MODEL` in
Lambda: the index records its model name and the runtime refuses a mismatch.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402
from rag.embedder import EmbeddingError, get_embedder  # noqa: E402
from rag.index import build_index  # noqa: E402
from rag.loader import load_documents  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["hosted", "local"], help="override EMBEDDING_BACKEND")
    parser.add_argument("--model", help="override EMBEDDING_MODEL")
    parser.add_argument("--documents", type=Path, help="override DOCUMENTS_DIR")
    parser.add_argument("--out", type=Path, help="override INDEX_PATH")
    args = parser.parse_args()

    # Applied before get_settings() so the overrides flow through one code path.
    if args.backend:
        os.environ["EMBEDDING_BACKEND"] = args.backend
    if args.model:
        os.environ["EMBEDDING_MODEL"] = args.model
    get_settings.cache_clear()

    settings = get_settings()
    documents = args.documents or settings.documents_dir
    out = args.out or settings.index_path

    # Built before the PDFs are read so a bad key fails in a second rather than
    # after a full chunking pass.
    try:
        embedder = get_embedder(settings)
    except EmbeddingError as exc:
        print(f"Cannot build the index: {exc}", file=sys.stderr)
        return 1

    # One throwaway vector before any real work: a wrong key, base URL or model
    # name is then a one-second failure rather than one that lands after the
    # whole corpus has been chunked and partly embedded.
    print(f"Checking {settings.embedding_backend}:{settings.embedding_model} ...")
    try:
        probe = await embedder.encode(["preflight"])
    except EmbeddingError as exc:
        print(f"Cannot build the index: {exc}", file=sys.stderr)
        return 1
    print(f"    ok — {probe.shape[1]} dimensions")

    chunks = load_documents(documents, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        print(f"No PDFs found in {documents}. Add documents and re-run.", file=sys.stderr)
        return 1

    pdf_count = len({chunk.source for chunk in chunks})
    print(f"Chunked {pdf_count} PDF(s) into {len(chunks)} chunks.")
    print(f"Embedding with {settings.embedding_backend}:{settings.embedding_model} ...")

    try:
        index = await build_index([chunk.as_dict() for chunk in chunks], embedder)
    except EmbeddingError as exc:
        # The traceback underneath this is provider plumbing, not information.
        print(f"\nEmbedding failed: {exc}", file=sys.stderr)
        return 1

    index.save(out)

    size_mb = out.stat().st_size / 1_048_576
    print(
        f"Wrote {out} — {len(index)} vectors x {index.vectors.shape[1]} dims "
        f"({size_mb:.1f} MB)."
    )
    print(
        "Set EMBEDDING_BACKEND and EMBEDDING_MODEL in Lambda to "
        f"'{settings.embedding_backend}' / '{index.model_name}'."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
