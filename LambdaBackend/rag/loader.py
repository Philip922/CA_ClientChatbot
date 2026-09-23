"""PDF ingestion and chunking.

Runs offline (see `scripts/build_index.py`), never in the Lambda request path —
the deployed function loads the prebuilt index instead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    """One embeddable passage, with enough metadata to cite it."""

    text: str
    source: str
    page: int
    chunk_index: int

    def as_dict(self) -> dict:
        return asdict(self)


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """Split into ~`size`-char windows with ~`overlap` chars of carry-over.

    Word-based rather than character-based: both the end of a chunk and the
    start of the next one land on a word boundary, so no chunk begins or ends
    mid-word. A cut token costs recall on exactly the term the user searched
    for, which is the term most likely to matter.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    normalised = " ".join(text.split())
    if not normalised:
        return []

    tokens = _tokenise(normalised, size)
    chunks: list[str] = []
    start = 0

    while start < len(tokens):
        end, length = start, 0
        while end < len(tokens):
            # Every token but the first also carries its preceding space.
            extra = len(tokens[end]) + (1 if end > start else 0)
            if length + extra > size:
                break
            length += extra
            end += 1
        end = max(end, start + 1)

        chunks.append(" ".join(tokens[start:end]))
        if end >= len(tokens):
            break

        start = _overlap_start(tokens, start, end, overlap)

    return [chunk for chunk in chunks if chunk]


def _tokenise(text: str, size: int) -> list[str]:
    """Words, with any token longer than one window hard-split to fit."""
    tokens: list[str] = []
    for word in text.split(" "):
        while len(word) > size:
            tokens.append(word[:size])
            word = word[size:]
        if word:
            tokens.append(word)
    return tokens


def _overlap_start(tokens: list[str], start: int, end: int, overlap: int) -> int:
    """Walk back from `end` while the carried text fits in `overlap` chars.

    Bounded at `start + 1` so the window always advances — an overlap wide
    enough to swallow the whole chunk must not loop forever.
    """
    carried = 0
    position = end
    while position > start + 1:
        candidate = len(tokens[position - 1]) + 1
        if carried + candidate > overlap:
            break
        carried += candidate
        position -= 1
    return position


def load_pdf(path: Path, size: int, overlap: int) -> list[Chunk]:
    """Extract and chunk one PDF, page by page."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks: list[Chunk] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for index, piece in enumerate(split_text(text, size, overlap)):
            chunks.append(
                Chunk(text=piece, source=path.name, page=page_number, chunk_index=index)
            )
    return chunks


def load_documents(directory: Path, size: int = 500, overlap: int = 50) -> list[Chunk]:
    """Chunk every PDF in `directory`, sorted by filename for a stable index."""
    if not directory.is_dir():
        raise FileNotFoundError(f"documents directory not found: {directory}")

    chunks: list[Chunk] = []
    for pdf in sorted(directory.glob("*.pdf")):
        chunks.extend(load_pdf(pdf, size, overlap))
    return chunks
