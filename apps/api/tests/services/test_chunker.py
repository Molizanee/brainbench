import pytest

from app.services.chunker import (
    MAX_CHARS,
    TARGET_CHARS,
    ChunkPiece,
    chunk_text,
    split_oversized,
    split_sentences,
)


def sentence(n: int, length: int = 300) -> str:
    """A sentence of exactly ``length`` chars ending in a period."""
    head = f"Sentence {n} "
    return head + "x" * (length - len(head) - 1) + "."


def words_sentence(length: int, word: str = "lorem") -> str:
    text = ""
    while len(text) < length - 1:
        text += word + " "
    return text[: length - 1].rstrip() + "."


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("  Raft elects a leader. It replicates a log.  ") == [
        ChunkPiece("Raft elects a leader. It replicates a log.")
    ]


def test_empty_text_has_no_chunks() -> None:
    assert chunk_text(" \n\n  ") == []


def test_sentences_are_packed_up_to_target_and_never_split() -> None:
    sentences = [sentence(i) for i in range(12)]
    text = " ".join(sentences)
    chunks = chunk_text(text)

    assert all(len(c.text) <= TARGET_CHARS for c in chunks)
    assert all(not c.sentence_continuation for c in chunks)
    # Every chunk is a run of whole sentences: 4 of 300 chars plus separators fit in 1500.
    assert [c.text for c in chunks] == [
        " ".join(sentences[0:4]),
        " ".join(sentences[4:8]),
        " ".join(sentences[8:12]),
    ]


def test_sentence_between_target_and_cap_stands_alone() -> None:
    long = sentence(1, 1800)
    chunks = chunk_text(f"Short one. {long} Short two.")
    assert [c.text for c in chunks] == ["Short one.", long, "Short two."]
    assert not any(c.sentence_continuation for c in chunks)


def test_sentence_of_exactly_the_cap_is_not_split() -> None:
    exact = words_sentence(MAX_CHARS)
    assert chunk_text(exact) == [ChunkPiece(exact)]


def test_oversized_sentence_with_whitespace_splits_at_last_whitespace() -> None:
    long = words_sentence(4500, "consensus")
    chunks = chunk_text(long)

    assert len(long) == 4500
    assert len(chunks) == 3
    assert all(c.sentence_continuation for c in chunks)
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert "".join(c.text for c in chunks) == long
    start = 0
    for chunk in chunks[:-1]:
        window = long[start : start + MAX_CHARS]
        last_space = max(i for i, ch in enumerate(window) if ch.isspace())
        assert len(chunk.text) == last_space + 1
        start += len(chunk.text)


def test_oversized_sentence_without_whitespace_splits_at_the_limit() -> None:
    long = "a" * 4500
    pieces = split_oversized(long)
    assert [len(p) for p in pieces] == [2000, 2000, 500]
    assert "".join(pieces) == long


def test_oversized_sentence_keeps_order_between_neighbours() -> None:
    long = words_sentence(4500)
    chunks = chunk_text(f"Before it. {long} After it.")
    assert chunks[0] == ChunkPiece("Before it.")
    assert chunks[-1] == ChunkPiece("After it.")
    middle = chunks[1:-1]
    assert all(c.sentence_continuation for c in middle)
    assert "".join(c.text for c in middle) == long


def test_paragraph_break_is_a_boundary_and_chunks_are_slices() -> None:
    first = "x" * 1000
    second = "y" * 1000
    text = f"{first}\n\n{second}"
    assert split_sentences(text) == [(0, 1000), (1002, 2002)]
    assert [c.text for c in chunk_text(text)] == [first, second]

    small = "First paragraph\n\nSecond paragraph"
    assert [c.text for c in chunk_text(small)] == [small]


def test_sentence_boundaries() -> None:
    text = 'He said "stop." Then left! Really? 終わり。次'
    spans = split_sentences(text)
    assert [text[a:b] for a, b in spans] == [
        'He said "stop."',
        "Then left!",
        "Really?",
        "終わり。",
        "次",
    ]
    # A period not followed by whitespace (versions, decimals) is not a boundary.
    assert len(split_sentences("Use v1.5 of the model, pi is 3.14 here.")) == 1


def test_every_chunk_respects_the_cap_on_mixed_input() -> None:
    parts = [sentence(i, 50 + (i * 97) % 2500) for i in range(40)]
    chunks = chunk_text(" ".join(parts) + " " + "z" * 5000)
    assert all(0 < len(c.text) <= MAX_CHARS for c in chunks)


def test_invalid_sizes() -> None:
    with pytest.raises(ValueError):
        chunk_text("x", target=3000, max_chars=2000)
