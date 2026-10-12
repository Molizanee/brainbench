"""Tutor reply on Postgres: real pgvector retrieval (topic first), real bge embeddings for the
related-topics step, JSONB persistence. The model is scripted; no provider is called.

Needs ``DATABASE_URL`` (Postgres with pgvector) and a writable ``BRAINBENCH_DATA`` for the
embedding model, like ``test_ingest_search.py``.
"""

import json
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.schema import (
    Base,
    Chunk,
    PlanItem,
    Source,
    StudyPlan,
    Topic,
    TopicChunk,
    TopicRevision,
    TutorMessage,
    TutorThread,
)
from app.services.embeddings import embed_texts
from app.services.tutor import TutorRuns, TutorService, reply_stream
from brainbench_agents.tutor.testing import ScriptedModel, ToolCall

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Postgres)"),
]

RAFT = (
    "Raft needs a majority of votes for leader election because any two majorities of the "
    "cluster overlap, so two candidates can never both win the same term."
)
COOKING = "Sourdough bread needs a starter of flour and water fed daily for a week."
REPLICATION = (
    "Log replication: the leader appends client commands to its log, sends them to followers, "
    "and commits an entry once a majority of servers store it."
)


@pytest.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ["DATABASE_URL"]
    schema = f"test_tutor_{uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
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


class Models:
    def __init__(self, model: ScriptedModel) -> None:
        self.scripted = model

    async def ensure(self, session: AsyncSession) -> None:
        return None

    async def model(self, session: AsyncSession) -> Any:
        return self.scripted


async def seed(session: AsyncSession) -> tuple[TutorThread, Any, Any]:
    raft = Source(type="note", title="Raft notes", note=RAFT, status="ready")
    bread = Source(type="note", title="Bread notes", note=COOKING, status="ready")
    session.add_all([raft, bread])
    await session.flush()
    vectors = embed_texts([RAFT, COOKING])
    raft_chunk = Chunk(source_id=raft.id, position=0, text=RAFT, location={}, embedding=vectors[0])
    session.add_all(
        [
            raft_chunk,
            Chunk(source_id=bread.id, position=0, text=COOKING, location={}, embedding=vectors[1]),
        ]
    )
    election, replication = Topic(), Topic()
    session.add_all([election, replication])
    await session.flush()
    session.add(TopicChunk(topic_id=election.id, chunk_id=raft_chunk.id))
    plan = StudyPlan(version=1, created_by="run", profile_version=1)
    session.add(plan)
    await session.flush()
    for position, (topic, title, summary) in enumerate(
        [
            (election, "Leader election", "Raft elects one leader per term by majority vote."),
            (replication, "Log replication", REPLICATION),
        ]
    ):
        (embedding,) = embed_texts([f"{title}\n{summary}"])
        revision = TopicRevision(
            topic_id=topic.id,
            revision=1,
            title=title,
            summary=summary,
            created_by="run",
            embedding=embedding,
        )
        session.add(revision)
        await session.flush()
        session.add(
            PlanItem(
                plan_id=plan.id,
                topic_id=topic.id,
                topic_revision_id=revision.id,
                position=position,
                week_start=position + 1,
                week_span=1,
            )
        )
    thread = TutorThread(context_type="topic", context_id=election.id)
    session.add(thread)
    await session.commit()
    return thread, election, replication


async def test_grounded_reply_on_postgres(pg_factory: async_sessionmaker[AsyncSession]) -> None:
    async with pg_factory() as session:
        thread, _, replication = await seed(session)
    model = ScriptedModel(
        [
            [ToolCall("search_chunks", {"query": "sourdough starter", "scope": "all"})],
            [f"{REPLICATION} [Raft notes, note]"],
        ]
    )
    async with pg_factory() as session:
        svc = TutorService(session, sessions=pg_factory, runs=TutorRuns(1), models=Models(model))
        run = await svc.send(thread.id, "Why does Raft need a majority for leader election?")
        received = []
        async for chunk in reply_stream(run, keepalive_s=30):
            if chunk.startswith("event: "):
                head, data = chunk.strip().split("\n", 1)
                received.append((head.removeprefix("event: "), json.loads(data[6:])))

    # Retrieval ran on pgvector: the topic's own chunk was given with the question.
    first_prompt = str(model.calls[0].messages[-1])
    assert "[Raft notes, note]" in first_prompt and "Sourdough" not in first_prompt
    # The tool searched every source and found the unrelated note on request.
    assert "[Bread notes, note]" in str(model.calls[1].messages[-1])
    kinds = [kind for kind, _ in received]
    assert kinds == ["delta", "citation", "related_topic", "done"]
    assert received[1][1]["source_title"] == "Raft notes" and received[1][1]["href"] is None
    related = received[2][1]
    assert related["topic_id"] == str(replication.id) and related["score"] >= 0.8

    async with pg_factory() as session:
        saved = list(
            await session.scalars(
                select(TutorMessage)
                .where(TutorMessage.thread_id == thread.id)
                .order_by(TutorMessage.created_at)
            )
        )
    assert [m.role for m in saved] == ["user", "assistant"]
    assert saved[1].citations[0]["label"] == "[Raft notes, note]"
    assert saved[1].related_topics[0]["title"] == "Log replication"
    assert saved[1].created_at.tzinfo is not None
