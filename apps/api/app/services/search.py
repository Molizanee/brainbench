"""Semantic search over chunks: cosine distance on pgvector's HNSW index (design D5).

``search_chunks`` is the retrieval primitive for the curation and tutor agents' ``search_chunks``
tools (sections 6 and 9) and for ``POST /api/search``.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import Chunk, Source, TopicChunk
from app.services import embeddings

MIN_K = 1
MAX_K = 50
# HNSW candidate list size; it must be at least k, and filters need headroom.
EF_SEARCH_FLOOR = 100


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: UUID
    source_id: UUID
    source_title: str
    location: dict[str, Any]
    text: str
    # Cosine similarity, 1 - cosine distance: higher is closer.
    score: float


async def search_chunks(
    session: AsyncSession,
    query: str,
    k: int,
    *,
    source_ids: Sequence[UUID] | None = None,
    topic_id: UUID | None = None,
) -> list[ChunkHit]:
    """Top-``k`` chunks most similar to ``query``, best first.

    ``source_ids`` restricts the search to those sources; ``topic_id`` to the chunks cited by
    that topic (``topic_chunks``). Both may be combined.
    """
    if not MIN_K <= k <= MAX_K:
        raise ValueError(f"k must be between {MIN_K} and {MAX_K}")
    vector = await asyncio.to_thread(embeddings.embed_query, query)
    return await nearest_chunks(session, vector, k, source_ids=source_ids, topic_id=topic_id)


async def nearest_chunks(
    session: AsyncSession,
    vector: list[float],
    k: int,
    *,
    source_ids: Sequence[UUID] | None = None,
    topic_id: UUID | None = None,
) -> list[ChunkHit]:
    """Top-``k`` chunks by cosine distance to an already computed query embedding."""
    distance = Chunk.embedding.cosine_distance(vector).label("distance")
    statement = (
        select(Chunk.id, Chunk.source_id, Source.title, Chunk.location, Chunk.text, distance)
        .join(Source, Source.id == Chunk.source_id)
        .where(Chunk.embedding.is_not(None))
        .order_by(distance, Chunk.id)
        .limit(k)
    )
    if source_ids is not None:
        statement = statement.where(Chunk.source_id.in_(list(source_ids)))
    if topic_id is not None:
        cited = select(TopicChunk.chunk_id).where(TopicChunk.topic_id == topic_id)
        statement = statement.where(Chunk.id.in_(cited))

    if session.get_bind().dialect.name == "postgresql":
        # Filtered HNSW scans keep going until k rows pass the filters, in exact order.
        await session.execute(
            select(
                func.set_config("hnsw.ef_search", str(max(EF_SEARCH_FLOOR, k)), True),
                func.set_config("hnsw.iterative_scan", "strict_order", True),
            )
        )
    rows = await session.execute(statement)
    return [
        ChunkHit(
            chunk_id=row.id,
            source_id=row.source_id,
            source_title=row.title,
            location=row.location,
            text=row.text,
            score=1.0 - float(row.distance),
        )
        for row in rows
    ]
