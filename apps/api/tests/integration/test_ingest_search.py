"""Ingestion and semantic search against Postgres with pgvector (``DATABASE_URL``).

Each test gets its own schema, so it can share a database with other suites. The last tests load
the real ``BAAI/bge-small-en-v1.5`` model from ``settings.models_dir`` (downloaded there on first
use), so ``BRAINBENCH_DATA`` must be writable.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from itertools import pairwise
from typing import Any
from uuid import UUID, uuid4

import pymupdf as _pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.schema import Base, Chunk, Source, Topic, TopicChunk
from app.db.session import get_session
from app.jobs.registry import HANDLERS, JobContext
from app.main import create_app
from app.services import embeddings
from app.services.search import nearest_chunks, search_chunks

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Postgres)"),
]

# pymupdf ships no annotations for most of its API.
pymupdf: Any = _pymupdf

DIM = 384


@pytest.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ["DATABASE_URL"]
    schema = f"test_ingest_{uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    # NullPool: the TestClient runs the app on its own event loop, and asyncpg connections are
    # bound to the loop that opened them. The translate map puts every table in the test schema
    # even when a migrated database already has them in public; public stays on the search path
    # for the vector type and its operators.
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
        execution_options={"schema_translate_map": {None: schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def axis(i: int, weight: float = 1.0, noise: float = 0.0) -> list[float]:
    vector = [0.0] * DIM
    vector[i] = weight
    vector[(i + 1) % DIM] = noise
    return vector


def rare_vector(i: int) -> list[float]:
    """Mostly axis 5, with an axis-0 component that shrinks as ``i`` grows."""
    vector = axis(5)
    vector[0] = 0.5 * (12 - i) / 12
    return vector


async def add_source(session: AsyncSession, title: str) -> Source:
    source = Source(id=uuid4(), type="note", note=title, title=title, status="ready")
    session.add(source)
    await session.flush()
    return source


async def test_cosine_ranking_k_and_filters(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_factory() as session:
        crowd = await add_source(session, "crowd")
        rare = await add_source(session, "rare")
        # 300 chunks close to the query in one source, 12 far ones in another: a plain HNSW
        # scan (ef_search 40) filtered to the far source would come back short.
        session.add_all(
            Chunk(
                source_id=crowd.id,
                position=i,
                text=f"crowd {i}",
                location={"page": i},
                embedding=axis(0, 1.0, noise=i / 300),
            )
            for i in range(300)
        )
        session.add_all(
            Chunk(
                source_id=rare.id,
                position=i,
                text=f"rare {i}",
                location={"path": f"f{i}.md"},
                embedding=rare_vector(i),
            )
            for i in range(12)
        )
        topic = Topic()
        session.add(topic)
        await session.flush()
        cited = await session.scalars(
            select(Chunk.id).where(Chunk.source_id == rare.id, Chunk.position.in_([3, 7]))
        )
        session.add_all(TopicChunk(topic_id=topic.id, chunk_id=c) for c in cited)
        await session.commit()

    async with pg_factory() as session:
        hits = await nearest_chunks(session, axis(0), 5)
        assert [h.text for h in hits] == [f"crowd {i}" for i in range(5)]
        assert [h.location for h in hits] == [{"page": i} for i in range(5)]
        assert hits[0].score == pytest.approx(1.0)
        assert all(a.score >= b.score for a, b in pairwise(hits))
        assert all(h.source_title == "crowd" and h.source_id == crowd.id for h in hits)

        assert len(await nearest_chunks(session, axis(0), 50)) == 50

        filtered = await nearest_chunks(session, axis(0), 10, source_ids=[rare.id])
        assert [h.text for h in filtered] == [f"rare {i}" for i in range(10)]

        by_topic = await nearest_chunks(session, axis(5), 10, topic_id=topic.id)
        assert [h.text for h in by_topic] == ["rare 7", "rare 3"]

        assert await nearest_chunks(session, axis(0), 5, source_ids=[]) == []


async def test_search_embeds_the_query(
    pg_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embeddings, "embed_query", lambda q: axis(len(q)))
    async with pg_factory() as session:
        source = await add_source(session, "s")
        session.add_all(
            Chunk(source_id=source.id, position=i, text=str(i), embedding=axis(i))
            for i in range(1, 6)
        )
        await session.commit()
        hits = await search_chunks(session, "abc", 2)
    # "abc" embeds onto axis 3: chunk "3" is identical, every other chunk is orthogonal.
    assert len(hits) == 2
    assert hits[0].text == "3"
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.0)


# --- Real model, full flow ----------------------------------------------------------------

PAGES = [
    "Raft is a consensus algorithm for managing a replicated log. A Raft cluster elects a "
    "leader: when a follower receives no heartbeat before its randomized election timeout "
    "expires, it becomes a candidate, increments its term, votes for itself and requests "
    "votes from the other servers. A candidate that collects votes from a majority becomes "
    "the leader for that term.",
    "Bloom filters are probabilistic data structures for set membership. They use several "
    "hash functions over a bit array and can return false positives but never false "
    "negatives. Their size is chosen from the expected number of items and the acceptable "
    "false positive rate.",
    "TCP congestion control adjusts the sending rate with a congestion window. Slow start "
    "grows the window exponentially until a threshold, after which congestion avoidance "
    "grows it linearly; packet loss shrinks it again.",
]


def make_pdf(pages: list[str]) -> bytes:
    document = pymupdf.Document()
    for page_text in pages:
        document.new_page().insert_textbox(pymupdf.Rect(72, 72, 540, 770), page_text)
    data: bytes = document.tobytes()
    document.close()
    return data


@pytest.fixture
def api(pg_factory: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    app = create_app(schema_guard=False)

    async def override() -> AsyncIterator[AsyncSession]:
        async with pg_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/setup", json={"password": "pw-integration"}).status_code == 201
        )
        yield client


async def run_job(factory: async_sessionmaker[AsyncSession], job_id: str, **kw: Any) -> None:
    async with factory() as session:
        source = await session.scalar(select(Source).where(Source.job_id == UUID(job_id)))
        assert source is not None
        ctx = JobContext(
            job_id=UUID(job_id),
            type="ingest",
            payload={"source_id": str(source.id)},
            checkpoint={},
            session=session,
            **kw,
        )
        await HANDLERS["ingest"](ctx)


async def test_pdf_ingested_and_searched_through_the_api(
    api: TestClient, pg_factory: async_sessionmaker[AsyncSession]
) -> None:
    import app.jobs  # noqa: F401

    created = api.post(
        "/api/sources", files={"file": ("systems.pdf", make_pdf(PAGES), "application/pdf")}
    ).json()
    assert created["status"] == "queued"
    await run_job(pg_factory, created["job_id"])

    [listed] = api.get("/api/sources").json()
    assert (listed["status"], listed["error"]) == ("ready", None)
    async with pg_factory() as session:
        rows = (
            await session.scalars(select(Chunk).where(Chunk.source_id == UUID(created["id"])))
        ).all()
    assert listed["chunk_count"] == len(rows) >= 3
    assert {r.location["page"] for r in rows} == {1, 2, 3}

    response = api.post("/api/search", json={"query": "Raft leader election", "k": 5})
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert 1 <= len(hits) <= 5
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert hits[0]["location"] == {"page": 1}
    assert hits[0]["source_id"] == created["id"]
    assert hits[0]["source_title"] == "systems.pdf"

    assert api.delete(f"/api/sources/{created['id']}").status_code == 204
    after = api.post("/api/search", json={"query": "Raft leader election", "k": 5}).json()
    assert after["hits"] == []


async def test_delete_during_ingestion_cancels_and_removes(
    api: TestClient, pg_factory: async_sessionmaker[AsyncSession]
) -> None:
    import app.jobs  # noqa: F401

    created = api.post("/api/sources", json={"type": "note", "text": PAGES[0]}).json()
    deleted = asyncio.Event()

    async def progress(message: str) -> None:
        # Delete from the API while the job is between extraction and embedding.
        if message.startswith("embedding") and not deleted.is_set():
            assert await asyncio.to_thread(api.delete, f"/api/sources/{created['id']}")
            deleted.set()

    await run_job(pg_factory, created["job_id"], progress_fn=progress)

    assert deleted.is_set()
    assert api.get("/api/sources").json() == []
    async with pg_factory() as session:
        assert (await session.scalars(select(Chunk))).all() == []
