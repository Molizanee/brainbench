"""Chunk storage: replaces a source's chunks with a new embedded set in batches."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import Chunk

INSERT_BATCH = 500


@dataclass(frozen=True)
class NewChunk:
    position: int
    text: str
    location: dict[str, Any] = field(default_factory=dict)
    sentence_continuation: bool = False


async def replace_chunks(
    session: AsyncSession,
    source_id: UUID,
    chunks: Sequence[NewChunk],
    embeddings: Sequence[list[float]],
) -> int:
    """Delete the source's chunks and insert ``chunks`` with their embeddings, in batches.

    Runs in the caller's transaction and does not commit, so a failure before the caller's
    commit leaves no partial set behind.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("every chunk needs exactly one embedding")
    await session.execute(delete(Chunk).where(Chunk.source_id == source_id))
    rows = [
        {
            "id": uuid4(),
            "source_id": source_id,
            "position": chunk.position,
            "text": chunk.text,
            "location": chunk.location,
            "sentence_continuation": chunk.sentence_continuation,
            "embedding": embedding,
        }
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    for start in range(0, len(rows), INSERT_BATCH):
        await session.execute(insert(Chunk), rows[start : start + INSERT_BATCH])
    return len(rows)
