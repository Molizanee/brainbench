"""Tutor service: streamed replies, stop and disconnect, the semaphore, limits, related topics.

The reply stream is consumed directly (``reply_stream``), because the HTTP test client buffers
a streamed body; ``tests/api/v1/test_tutor.py`` covers the same flow through HTTP.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.schema import (
    Asset,
    PlanItem,
    Profile,
    Source,
    StudyPlan,
    Topic,
    TopicRevision,
    TutorMessage,
    TutorThread,
)
from app.services import embeddings, search
from app.services.search import ChunkHit
from app.services.tutor import (
    ContextRef,
    InvalidContext,
    TutorBusy,
    TutorRun,
    TutorRuns,
    TutorService,
    citation_href,
    cosine,
    history_messages,
    parse_context,
    related_topics,
    reply_stream,
)
from brainbench_agents.tutor import NO_MATERIAL, ChunkRef
from brainbench_agents.tutor.testing import Pause, Raise, ScriptedModel, Turn
from brainbench_providers.strands_models import ProviderThrottledException

PDF = uuid.UUID("00000000-0000-4000-8000-000000000001")
DIM = 384


def unit(index: int, other: int | None = None) -> list[float]:
    vector = [0.0] * DIM
    vector[index] = 1.0
    if other is not None:
        vector[other] = 0.3
    return vector


class FakeModels:
    def __init__(self, model: ScriptedModel) -> None:
        self.scripted = model

    async def ensure(self, session: AsyncSession) -> None:
        return None

    async def model(self, session: AsyncSession) -> Any:
        return self.scripted


@pytest.fixture
def hits(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[ChunkHit]]:
    """Search results by scope: ``topic`` for topic-scoped searches, ``all`` otherwise."""
    results: dict[str, list[ChunkHit]] = {"topic": [], "all": []}

    async def fake_search(
        session: AsyncSession, query: str, k: int, *, topic_id: uuid.UUID | None = None, **_: Any
    ) -> list[ChunkHit]:
        return list(results["topic" if topic_id else "all"])[:k]

    monkeypatch.setattr(search, "search_chunks", fake_search)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [unit(1, 0) for _ in texts])
    return results


@pytest.fixture
async def thread(session: AsyncSession) -> TutorThread:
    session.add(Source(id=PDF, type="pdf", title="Raft paper", file_path=f"sources/{PDF}.pdf"))
    session.add(
        Profile(display_name="Ada", weekly_budget_hours=5, language="pt-BR", depth="rigor-first")
    )
    row = TutorThread(context_type="general")
    session.add(row)
    await session.commit()
    return row


def service(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    model: ScriptedModel,
    runs: TutorRuns | None = None,
) -> TutorService:
    return TutorService(
        session, sessions=factory, runs=runs or TutorRuns(1), models=FakeModels(model)
    )


async def events(run: TutorRun) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    async for chunk in reply_stream(run, keepalive_s=5):
        if chunk.startswith(":"):
            continue
        head, data = chunk.strip().split("\n", 1)
        yield head.removeprefix("event: "), json.loads(data.removeprefix("data: "))


async def collect(run: TutorRun) -> list[tuple[str, dict[str, Any]]]:
    return [event async for event in events(run)]


def hit(page: int, text: str, score: float = 0.8) -> ChunkHit:
    return ChunkHit(uuid.uuid4(), PDF, "Raft paper", {"page": page}, text, score)


async def stored(factory: async_sessionmaker[AsyncSession], thread_id: uuid.UUID) -> list[Any]:
    async with factory() as s:
        rows = await s.scalars(
            select(TutorMessage)
            .where(TutorMessage.thread_id == thread_id)
            .order_by(TutorMessage.created_at)
        )
        return list(rows)


# --- pure helpers ---------------------------------------------------------------------------


def test_parse_context() -> None:
    topic = uuid.uuid4()
    assert parse_context(None) == ContextRef("general")
    assert parse_context("plan") == ContextRef("plan")
    assert parse_context(f"topic:{topic}") == ContextRef("topic", topic)
    for bad in ("topic:", "topic:nope", "page", "topic"):
        with pytest.raises(InvalidContext):
            parse_context(bad)


def test_citation_hrefs_point_at_the_location() -> None:
    def ref(kind: str, uri: str | None, location: dict[str, Any]) -> ChunkRef:
        return ChunkRef("c", str(PDF), "T", location, "text", 0.9, kind, uri)

    assert citation_href(ref("pdf", None, {"page": 3})) == f"/api/tutor/sources/{PDF}/file#page=3"
    assert citation_href(ref("url", "https://x.dev/a#old", {"fragment": "#:~:text=Raft"})) == (
        "https://x.dev/a#:~:text=Raft"
    )
    assert citation_href(ref("git", "https://github.com/o/r.git", {"path": "docs/a b.md"})) == (
        "https://github.com/o/r/blob/HEAD/docs/a%20b.md"
    )
    assert citation_href(ref("git", "https://example.org/r", {"path": "a.md"})) == (
        "https://example.org/r"
    )
    assert citation_href(ref("note", None, {})) is None


def test_history_alternates_and_ends_with_a_reply() -> None:
    def m(role: str, content: str) -> TutorMessage:
        return TutorMessage(role=role, content=content)

    rows = [
        m("assistant", "orphan"),
        m("user", "q1"),
        m("user", "q1b"),
        m("assistant", "a1"),
        m("assistant", ""),
        m("user", "q2"),
    ]
    turns = history_messages(rows)
    assert turns == [
        {"role": "user", "content": [{"text": "q1\n\nq1b"}]},
        {"role": "assistant", "content": [{"text": "a1"}]},
    ]


def test_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([0, 0], [1, 0]) == 0.0


# --- streamed replies -------------------------------------------------------------------------


async def test_grounded_reply_streams_deltas_and_citations(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    hits["all"] = [hit(5, "A candidate needs votes from a majority."), hit(9, "Terms.", 0.4)]
    model = ScriptedModel([["Two majorities ", "always overlap [Raft paper, p. 5]", "."]])
    svc = service(session, session_factory, model)
    run = await svc.send(thread.id, "Why does Raft need a majority?")
    received = await collect(run)
    kinds = [kind for kind, _ in received]
    assert kinds == ["delta", "delta", "citation", "delta", "done"]
    citation = received[2][1]
    assert citation["label"] == "[Raft paper, p. 5]"
    assert citation["href"] == f"/api/tutor/sources/{PDF}/file#page=5"
    assert citation["excerpt"] == "A candidate needs votes from a majority."
    done = received[-1][1]
    assert done["message"]["content"] == "Two majorities always overlap [Raft paper, p. 5]."
    assert done["message"]["stopped"] is False and done["message"]["citations"] == [citation]
    prompt = str(model.calls[0].messages[-1])
    # The low-scoring passage is not shown to the model.
    assert "[Raft paper, p. 5]" in prompt and "Terms." not in prompt
    system = model.calls[0].system_prompt or ""
    assert "pt-BR" in system and "Lead with the precise definition" in system
    question, answer = await stored(session_factory, thread.id)
    assert (question.role, question.content) == ("user", "Why does Raft need a majority?")
    assert answer.citations == [citation] and answer.usage["output_tokens"] == 20
    assert answer.usage["model"] == "scripted"


async def test_material_gap_is_stated_without_fabricated_citations(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    hits["all"] = [hit(2, "Unrelated passage.", 0.3)]
    model = ScriptedModel(
        [["Your material does not cover CRDTs. ", "In general, [Shapiro et al., p. 1] ..."]]
    )
    run = await service(session, session_factory, model).send(thread.id, "What is a CRDT?")
    received = await collect(run)
    assert NO_MATERIAL in str(model.calls[0].messages[-1])
    assert [kind for kind, _ in received] == ["delta", "delta", "done"]
    message = received[-1][1]["message"]
    assert message["content"].startswith("Your material does not cover CRDTs.")
    assert message["citations"] == []


async def test_stop_persists_the_partial_reply_as_stopped(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    model = ScriptedModel([Turn(["Raft elects ", "a leader ", Pause(0.5), "never sent"])])
    svc = service(session, session_factory, model)
    run = await svc.send(thread.id, "Explain leader election")
    stream = events(run)
    assert await anext(stream) == ("delta", {"text": "Raft elects "})
    assert await anext(stream) == ("delta", {"text": "a leader "})
    started = asyncio.get_running_loop().time()
    result = await svc.stop(thread.id)
    assert asyncio.get_running_loop().time() - started < 5
    assert result.stopped and result.message is not None
    assert result.message.content == "Raft elects a leader "
    assert result.message.stopped and result.message.interrupted == "stop"
    rest = [event async for event in stream]
    assert [kind for kind, _ in rest] == ["done"]
    assert rest[0][1]["message"]["stopped"] is True
    _, answer = await stored(session_factory, thread.id)
    assert (answer.content, answer.stopped) == ("Raft elects a leader ", True)
    # Nothing is in flight any more: stopping again reports the saved reply.
    again = await svc.stop(thread.id)
    assert not again.stopped and again.message is not None and again.message.stopped


async def test_stop_cancels_a_silent_provider_within_the_grace_period(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tutor_stop_grace_s", 0.2)
    model = ScriptedModel([Turn(["Partial ", Pause(60), "never"])])
    svc = service(session, session_factory, model)
    run = await svc.send(thread.id, "Explain")
    stream = events(run)
    await anext(stream)
    started = asyncio.get_running_loop().time()
    result = await svc.stop(thread.id)
    assert asyncio.get_running_loop().time() - started < 2
    assert result.message is not None and result.message.content == "Partial "
    assert result.message.stopped


async def test_client_disconnect_stops_and_saves_the_reply(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    model = ScriptedModel([Turn(["Half ", Pause(0.5), "never"])])
    run = await service(session, session_factory, model).send(thread.id, "Explain")
    stream = reply_stream(run, keepalive_s=5)
    assert await anext(stream) == ": reply\n\n"
    await anext(stream)
    await stream.aclose()
    await asyncio.wait_for(run.finished.wait(), 5)
    assert run.message is not None and run.message.interrupted == "disconnect"
    _, answer = await stored(session_factory, thread.id)
    assert (answer.content, answer.stopped) == ("Half ", True)


async def test_one_reply_in_flight(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    runs = TutorRuns(1)
    model = ScriptedModel([Turn(["Slow ", Pause(0.3), "reply."]), ["Second."]])
    svc = service(session, session_factory, model, runs)
    first = await svc.send(thread.id, "First")
    with pytest.raises(TutorBusy):
        await svc.send(thread.id, "Second")
    assert len(await stored(session_factory, thread.id)) == 1
    await collect(first)
    second = await svc.send(thread.id, "Second")
    assert [k for k, _ in await collect(second)] == ["delta", "done"]
    # The second turn saw the first exchange as history.
    assert [m["role"] for m in model.calls[1].messages] == ["user", "assistant", "user"]


async def test_throttle_reports_the_reset_and_withdraws_the_question(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    reset = datetime.now(UTC) + timedelta(hours=2)
    limit = ProviderThrottledException("usage limit", reset_at=reset, scope="all-accounts")
    model = ScriptedModel([Turn([Raise(limit)])])
    run = await service(session, session_factory, model).send(thread.id, "Explain")
    received = await collect(run)
    assert [kind for kind, _ in received] == ["limit"]
    payload = received[0][1]
    assert datetime.fromisoformat(payload["reset_at"]) == reset
    assert payload["scope"] == "all-accounts" and payload["reply"] is None
    assert await stored(session_factory, thread.id) == []


async def test_throttle_mid_reply_keeps_the_partial_text(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    limit = ProviderThrottledException("limit", reset_at=None, scope="all-accounts")
    model = ScriptedModel([Turn(["Partial ", Raise(limit)])])
    run = await service(session, session_factory, model).send(thread.id, "Explain")
    received = await collect(run)
    assert [kind for kind, _ in received] == ["delta", "limit"]
    reply = received[-1][1]["reply"]
    assert reply["content"] == "Partial " and reply["interrupted"] == "limit" and reply["stopped"]
    assert len(await stored(session_factory, thread.id)) == 2


async def test_unexpected_failure_sends_an_error_event(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    thread: TutorThread,
    hits: dict[str, list[ChunkHit]],
) -> None:
    model = ScriptedModel([Turn([Raise(RuntimeError("boom"))])])
    run = await service(session, session_factory, model).send(thread.id, "Explain")
    received = await collect(run)
    failed = {"code": "tutor-failed", "message": "The tutor failed: RuntimeError", "reply": None}
    assert received == [("error", failed)]
    assert await stored(session_factory, thread.id) == []


# --- related topics ---------------------------------------------------------------------------


async def plan_with_topics(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Three plan topics: A (index 0), B (index 1, has a video), C (excluded, index 1)."""
    ids = []
    plan = StudyPlan(version=1, created_by="run", profile_version=1)
    session.add(plan)
    for position, (title, axis, excluded) in enumerate(
        [("Leader election", 0, False), ("Log replication", 1, False), ("Snapshots", 1, True)]
    ):
        topic = Topic()
        session.add(topic)
        await session.flush()
        revision = TopicRevision(
            topic_id=topic.id,
            revision=1,
            title=title,
            summary=title,
            created_by="run",
            embedding=unit(axis),
        )
        session.add(revision)
        await session.flush()
        session.add(
            PlanItem(
                plan_id=plan.id,
                topic_id=topic.id,
                topic_revision_id=revision.id,
                position=position,
                excluded=excluded,
            )
        )
        ids.append(topic.id)
    session.add(Asset(topic_id=ids[1], format="video"))
    await session.commit()
    return ids[0], ids[1], ids[2]


async def test_related_topics_by_embedding_similarity(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, _ = await plan_with_topics(session)
    texts: list[str] = []

    def embed(batch: list[str]) -> list[list[float]]:
        texts.extend(batch)
        return [unit(1, 0) for _ in batch]

    monkeypatch.setattr(embeddings, "embed_texts", embed)
    related = await related_topics(session, "Entries replicate [Raft paper, p. 5].")
    # cos(B) = 1/sqrt(1.09) ~ 0.958; cos(A) ~ 0.287; the excluded topic is never offered.
    assert [(r.topic_id, r.title, r.assets) for r in related] == [(b, "Log replication", ["video"])]
    assert texts == ["Entries replicate ."]
    assert await related_topics(session, "x", exclude=b) == []
    assert [r.topic_id for r in await related_topics(session, "x", threshold=0.2)] == [b, a]


async def test_reply_emits_related_topics_except_the_context_topic(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    hits: dict[str, list[ChunkHit]],
) -> None:
    a, b, _ = await plan_with_topics(session)
    thread = TutorThread(context_type="topic", context_id=a)
    session.add(thread)
    await session.commit()
    model = ScriptedModel([["Followers append entries."]])
    run = await service(session, session_factory, model).send(thread.id, "How do logs replicate?")
    received = await collect(run)
    assert [kind for kind, _ in received] == ["delta", "related_topic", "done"]
    assert received[1][1]["topic_id"] == str(b)
    assert received[-1][1]["message"]["related_topics"][0]["title"] == "Log replication"
    assert '"Leader election"' in (model.calls[0].system_prompt or "")
