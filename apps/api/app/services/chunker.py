"""Sentence-aware chunker (design D5). Pure: no I/O, no settings.

Text is split into sentences (sentence-ending punctuation followed by whitespace, and paragraph
breaks). Consecutive sentences are packed into a chunk while it stays within ``target`` chars;
a sentence that alone exceeds ``max_chars`` is cut into pieces of at most ``max_chars``, each at
the last whitespace before the limit (or at the limit when the span has none), and every piece
becomes its own chunk flagged ``sentence_continuation``. Chunks are exact slices of the input,
so pieces of one sentence concatenate back to that sentence.
"""

import re
from dataclasses import dataclass

TARGET_CHARS = 1500
MAX_CHARS = 2000

# A sentence ends after . ! ? or an ellipsis, optionally followed by closing quotes or brackets,
# when whitespace follows; CJK full stops end a sentence without whitespace. A blank line ends a
# paragraph and therefore a sentence.
_BOUNDARY = re.compile(
    r"(?:[.!?\u2026]+[\"'\u201d\u2019)\]]*(?=\s)|[\u3002\uff01\uff1f]+)|\n[ \t]*\n"
)


@dataclass(frozen=True)
class ChunkPiece:
    text: str
    sentence_continuation: bool = False


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of the sentences in ``text``, without surrounding space."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        end = match.end() if not match.group().startswith("\n") else match.start()
        _append_trimmed(text, start, end, spans)
        start = match.end()
    _append_trimmed(text, start, len(text), spans)
    return spans


def _append_trimmed(text: str, start: int, end: int, spans: list[tuple[int, int]]) -> None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((start, end))


def split_oversized(sentence: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Cut one sentence into pieces of at most ``max_chars`` whose concatenation is the input.

    Each cut falls just after the last whitespace in the next ``max_chars`` characters, or at
    ``max_chars`` when that span has no whitespace (or only a leading one).
    """
    pieces: list[str] = []
    start = 0
    while len(sentence) - start > max_chars:
        limit = start + max_chars
        space = next((i for i in range(limit - 1, start, -1) if sentence[i].isspace()), None)
        cut = space + 1 if space is not None else limit
        pieces.append(sentence[start:cut])
        start = cut
    pieces.append(sentence[start:])
    return pieces


def chunk_text(
    text: str, *, target: int = TARGET_CHARS, max_chars: int = MAX_CHARS
) -> list[ChunkPiece]:
    """Split ``text`` into ordered chunks of at most ``max_chars`` characters."""
    if not 0 < target <= max_chars:
        raise ValueError("expected 0 < target <= max_chars")
    chunks: list[ChunkPiece] = []
    group: tuple[int, int] | None = None

    def flush() -> None:
        nonlocal group
        if group is not None:
            chunks.append(ChunkPiece(text[group[0] : group[1]]))
            group = None

    for start, end in split_sentences(text):
        if end - start > max_chars:
            flush()
            chunks.extend(
                ChunkPiece(piece, sentence_continuation=True)
                for piece in split_oversized(text[start:end], max_chars)
            )
        elif group is not None and end - group[0] <= target:
            group = (group[0], end)
        else:
            flush()
            group = (start, end)
    flush()
    return chunks
