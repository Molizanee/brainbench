import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import Chunk, Source
from app.services import chunks as chunks_module
from app.services.chunks import NewChunk, replace_chunks


def vec(x: float) -> list[float]:
    return [x] + [0.0] * 383


async def test_replace_chunks_inserts_in_batches_and_replaces(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chunks_module, "INSERT_BATCH", 2)
    source = Source(type="note", note="n", title="n")
    session.add(source)
    await session.flush()

    first = [NewChunk(i, f"old {i}") for i in range(3)]
    assert await replace_chunks(session, source.id, first, [vec(i) for i in range(3)]) == 3
    new = [
        NewChunk(0, "a", {"page": 1}),
        NewChunk(1, "b", {"page": 2}, sentence_continuation=True),
        NewChunk(2, "c", {"page": 2}, sentence_continuation=True),
        NewChunk(3, "d", {"page": 3}),
        NewChunk(4, "e", {"page": 3}),
    ]
    assert await replace_chunks(session, source.id, new, [vec(i) for i in range(5)]) == 5
    await session.commit()

    rows = (await session.scalars(select(Chunk).order_by(Chunk.position))).all()
    assert [(r.position, r.text, r.location, r.sentence_continuation) for r in rows] == [
        (0, "a", {"page": 1}, False),
        (1, "b", {"page": 2}, True),
        (2, "c", {"page": 2}, True),
        (3, "d", {"page": 3}, False),
        (4, "e", {"page": 3}, False),
    ]
    assert rows[4].embedding is not None and list(rows[4].embedding)[:2] == [4.0, 0.0]


async def test_replace_chunks_requires_one_embedding_per_chunk(session: AsyncSession) -> None:
    source = Source(type="note", note="n", title="n")
    session.add(source)
    await session.flush()
    with pytest.raises(ValueError, match="exactly one embedding"):
        await replace_chunks(session, source.id, [NewChunk(0, "a")], [])
