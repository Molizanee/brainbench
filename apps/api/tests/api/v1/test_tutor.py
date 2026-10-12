import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import require_session
from app.api.v1 import tutor as tutor_router
from app.core.config import settings
from app.db.schema import CurrentSkill, Profile, Source, Topic, TopicRevision, TutorMessage
from app.services import embeddings, search
from app.services.providers import ProviderRuntime, get_provider_runtime, get_registry
from app.services.search import ChunkHit
from app.services.tutor import TutorRuns
from brainbench_agents.tutor.testing import Raise, ScriptedModel, Turn
from brainbench_providers.strands_models import ProviderThrottledException
from brainbench_providers.testing import FakeProviders

PDF = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


class FakeModels:
    def __init__(self) -> None:
        self.scripted = ScriptedModel([])

    def script(self, *turns: Turn | list[Any]) -> ScriptedModel:
        self.scripted.turns.extend(t if isinstance(t, Turn) else Turn(t) for t in turns)
        return self.scripted

    async def ensure(self, session: AsyncSession) -> None:
        return None

    async def model(self, session: AsyncSession) -> Any:
        return self.scripted


@pytest.fixture
def models() -> FakeModels:
    return FakeModels()


@pytest.fixture
def hits(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[ChunkHit]]:
    results: dict[str, list[ChunkHit]] = {"topic": [], "all": []}

    async def fake_search(
        session: AsyncSession, query: str, k: int, *, topic_id: uuid.UUID | None = None, **_: Any
    ) -> list[ChunkHit]:
        return list(results["topic" if topic_id else "all"])

    monkeypatch.setattr(search, "search_chunks", fake_search)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[1.0] * 384 for _ in texts])
    return results


@pytest.fixture
def api(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    models: FakeModels,
    hits: dict[str, list[ChunkHit]],
) -> Iterator[TestClient]:
    app.dependency_overrides[require_session] = lambda: None
    app.dependency_overrides[tutor_router.get_tutor_sessions] = lambda: session_factory
    app.dependency_overrides[tutor_router.get_tutor_runs] = lambda: TutorRuns(1)
    app.dependency_overrides[tutor_router.get_tutor_models] = lambda: models
    with TestClient(app) as c:
        yield c


async def add_topic(session: AsyncSession, title: str) -> uuid.UUID:
    topic = Topic()
    session.add(topic)
    await session.flush()
    session.add(
        TopicRevision(topic_id=topic.id, revision=1, title=title, summary="s", created_by="run")
    )
    await session.commit()
    return topic.id


def sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        fields = dict(line.split(": ", 1) for line in lines)
        out.append((fields["event"], json.loads(fields["data"])))
    return out


def send(api: TestClient, thread_id: str, content: str) -> Any:
    return api.post(f"/api/tutor/threads/{thread_id}/messages", json={"content": content})


def test_tutor_routes_require_a_session(client: TestClient) -> None:
    assert client.get("/api/tutor/threads").status_code == 401


def test_threads_per_context_and_new_thread(api: TestClient, session: AsyncSession) -> None:
    general = api.get("/api/tutor/threads").json()
    assert general == {
        "context": {
            "type": "general",
            "topic_id": None,
            "title": None,
            "label": "General",
            "assets": [],
        },
        "threads": [],
    }
    created = api.post("/api/tutor/threads?context=plan")
    assert created.status_code == 201
    plan = api.get("/api/tutor/threads?context=plan").json()
    assert plan["context"]["label"] == "Study plan"
    assert [t["id"] for t in plan["threads"]] == [created.json()["id"]]
    assert api.get("/api/tutor/threads?context=general").json()["threads"] == []
    newer = api.post("/api/tutor/threads?context=plan").json()
    listed = api.get("/api/tutor/threads?context=plan").json()["threads"]
    assert [t["id"] for t in listed] == [newer["id"], created.json()["id"]]


def test_invalid_and_unknown_contexts(api: TestClient) -> None:
    bad = api.get("/api/tutor/threads?context=topic:nope")
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid-context"
    missing = api.get(f"/api/tutor/threads?context=topic:{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "topic-not-found"
    assert api.get(f"/api/tutor/threads/{uuid.uuid4()}/messages").status_code == 404
    assert send(api, str(uuid.uuid4()), "Hi").status_code == 404


async def test_thread_per_topic_is_restored_with_its_messages(
    api: TestClient, session: AsyncSession, models: FakeModels
) -> None:
    raft = await add_topic(session, "Raft leader election")
    paxos = await add_topic(session, "Paxos")
    thread = api.post(f"/api/tutor/threads?context=topic:{raft}").json()
    models.script(["Votes from a majority."])
    assert send(api, thread["id"], "Why a majority?").status_code == 200
    # Returning later (a new request, nothing cached client-side) restores the thread.
    listed = api.get(f"/api/tutor/threads?context=topic:{raft}").json()
    assert listed["context"]["label"] == "Raft leader election"
    assert listed["context"]["topic_id"] == str(raft)
    assert [t["id"] for t in listed["threads"]] == [thread["id"]]
    assert listed["threads"][0]["title"] == "Why a majority?"
    messages = api.get(f"/api/tutor/threads/{thread['id']}/messages").json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "Why a majority?"),
        ("assistant", "Votes from a majority."),
    ]
    assert api.get(f"/api/tutor/threads?context=topic:{paxos}").json()["threads"] == []


async def test_merged_topic_context_resolves_to_the_survivor(
    api: TestClient, session: AsyncSession
) -> None:
    survivor = await add_topic(session, "Consensus")
    merged = await add_topic(session, "Old consensus")
    topic = await session.get(Topic, merged)
    assert topic is not None
    topic.merged_into = survivor
    await session.commit()
    created = api.post(f"/api/tutor/threads?context=topic:{merged}").json()
    assert created["context_id"] == str(survivor)
    listed = api.get(f"/api/tutor/threads?context=topic:{merged}").json()
    assert listed["context"]["label"] == "Consensus"


async def test_message_streams_named_sse_events_with_citations(
    api: TestClient,
    session: AsyncSession,
    models: FakeModels,
    hits: dict[str, list[ChunkHit]],
) -> None:
    session.add(Source(id=PDF, type="pdf", title="Raft paper", file_path=f"sources/{PDF}.pdf"))
    await session.commit()
    hits["all"] = [ChunkHit(uuid.uuid4(), PDF, "Raft paper", {"page": 5}, "Majority.", 0.9)]
    thread = api.post("/api/tutor/threads").json()
    models.script(["Any two majorities overlap ", "[Raft paper, p. 5]."])
    response = send(api, thread["id"], "Why does Raft need a majority?")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    received = sse_events(response.text)
    assert [kind for kind, _ in received] == ["delta", "delta", "citation", "done"]
    assert received[2][1]["href"] == f"/api/tutor/sources/{PDF}/file#page=5"
    done = received[-1][1]
    stored = api.get(f"/api/tutor/threads/{thread['id']}/messages").json()
    # SQLite drops the time zone on read; everything else is what the stream announced.
    assert {**stored[-1], "created_at": None} == {**done["message"], "created_at": None}
    assert stored[0]["id"] == done["user_message_id"]


def test_blank_message_is_rejected(api: TestClient) -> None:
    thread = api.post("/api/tutor/threads").json()
    response = send(api, thread["id"], "   ")
    assert response.status_code == 422


def test_limit_event_through_http(api: TestClient, models: FakeModels) -> None:
    thread = api.post("/api/tutor/threads").json()
    limit = ProviderThrottledException("limit", reset_at=None, scope="all-accounts")
    models.script(Turn([Raise(limit)]))
    received = sse_events(send(api, thread["id"], "Explain").text)
    assert received[0][0] == "limit" and received[0][1]["reset_at"] is None
    assert api.get(f"/api/tutor/threads/{thread['id']}/messages").json() == []


def test_stop_without_a_reply_in_flight(api: TestClient) -> None:
    thread = api.post("/api/tutor/threads").json()
    response = api.post(f"/api/tutor/threads/{thread['id']}/stop")
    assert response.status_code == 200
    assert response.json() == {"stopped": False, "message": None}


async def test_source_file_serves_the_stored_pdf(
    api: TestClient,
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / f"{PDF}.pdf").write_bytes(b"%PDF-1.7 fixture")
    session.add(Source(id=PDF, type="pdf", title="Raft", file_path=f"sources/{PDF}.pdf"))
    note = Source(type="note", title="Note", note="text")
    escape = Source(type="pdf", title="Escape", file_path="../outside.pdf")
    session.add_all([note, escape])
    await session.commit()
    response = api.get(f"/api/tutor/sources/{PDF}/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content == b"%PDF-1.7 fixture"
    for other in (note.id, escape.id, uuid.uuid4()):
        missing = api.get(f"/api/tutor/sources/{other}/file")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "source-file-not-found"


# --- no provider (the real provider check, no fake models) -----------------------------------


@pytest.fixture
def no_provider_api(
    app: FastAPI, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> Iterator[TestClient]:
    runtime = ProviderRuntime(
        get_registry(),
        session_factory,
        tmp_path / "vault.key",
        http=FakeProviders().http_factory(),
    )
    app.dependency_overrides[require_session] = lambda: None
    app.dependency_overrides[get_provider_runtime] = lambda: runtime
    app.dependency_overrides[tutor_router.get_tutor_sessions] = lambda: session_factory
    app.dependency_overrides[tutor_router.get_tutor_runs] = lambda: TutorRuns(1)
    with TestClient(app) as c:
        yield c


async def test_no_provider_rejects_the_message_before_storing_it(
    no_provider_api: TestClient, session: AsyncSession
) -> None:
    thread = no_provider_api.post("/api/tutor/threads").json()
    response = send(no_provider_api, thread["id"], "Explain Raft")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "no-provider" and "/providers" in detail["message"]
    assert "tutoring" in detail["message"]
    assert list(await session.scalars(select(TutorMessage))) == []


# --- tutor-chat spec scenarios ----------------------------------------------------------------


async def test_scenario_topic_context_attached_draws_on_topic_chunks_first(
    api: TestClient,
    session: AsyncSession,
    models: FakeModels,
    hits: dict[str, list[ChunkHit]],
) -> None:
    raft = await add_topic(session, "Raft leader election")
    session.add(Source(id=PDF, type="pdf", title="Raft paper", file_path=f"sources/{PDF}.pdf"))
    await session.commit()
    topic_chunk = ChunkHit(uuid.uuid4(), PDF, "Raft paper", {"page": 5}, "Topic passage.", 0.7)
    other = ChunkHit(uuid.uuid4(), PDF, "Raft paper", {"page": 1}, "Better global match.", 0.95)
    hits["topic"] = [topic_chunk]
    hits["all"] = [other, topic_chunk]
    listed = api.get(f"/api/tutor/threads?context=topic:{raft}").json()
    assert listed["context"]["label"] == "Raft leader election"
    thread = api.post(f"/api/tutor/threads?context=topic:{raft}").json()
    model = models.script(["Answer."])
    send(api, thread["id"], "Why a majority?")
    prompt = model.calls[0].messages[-1]["content"][0]["text"]
    assert prompt.index("Topic passage.") < prompt.index("Better global match.")
    assert '"Raft leader election"' in (model.calls[0].system_prompt or "")


async def test_scenario_material_gap_stated_when_retrieval_is_empty(
    api: TestClient, models: FakeModels, hits: dict[str, list[ChunkHit]]
) -> None:
    thread = api.post("/api/tutor/threads").json()
    model = models.script(["Your material does not cover CRDTs. In general, a CRDT ..."])
    received = sse_events(send(api, thread["id"], "What is a CRDT?").text)
    assert "does not cover" in model.calls[0].messages[-1]["content"][0]["text"]
    assert [kind for kind, _ in received] == ["delta", "done"]
    message = received[-1][1]["message"]
    assert message["content"].startswith("Your material does not cover")
    assert message["citations"] == []


async def test_scenario_depth_preference_and_known_prerequisites_reach_the_tutor(
    api: TestClient, session: AsyncSession, models: FakeModels
) -> None:
    profile = Profile(display_name="Ada", weekly_budget_hours=5, depth="intuition-first")
    session.add(profile)
    await session.flush()
    session.add(
        CurrentSkill(
            profile_id=profile.id,
            name="hash functions",
            name_normalized="hash functions",
            level="expert",
        )
    )
    await session.commit()
    thread = api.post("/api/tutor/threads").json()
    model = models.script(["Picture a ring."])
    send(api, thread["id"], "Explain consistent hashing")
    system = model.calls[0].system_prompt or ""
    assert "intuitive picture or analogy in plain words before any formal definition" in system
    assert "proficient or expert in: hash functions" in system
    assert "never define or re-explain them" in system


async def test_scenario_provider_limit_then_resend_after_reset(
    api: TestClient, models: FakeModels
) -> None:
    thread = api.post("/api/tutor/threads").json()
    reset = "2026-10-12T18:00:00+00:00"
    limit = ProviderThrottledException(
        "limit", reset_at=datetime.fromisoformat(reset), scope="all-accounts"
    )
    models.script(Turn([Raise(limit)]), ["Raft elects a leader."])
    limited = sse_events(send(api, thread["id"], "Explain Raft").text)
    assert limited[0][0] == "limit"
    assert datetime.fromisoformat(limited[0][1]["reset_at"]) == datetime.fromisoformat(reset)
    resent = sse_events(send(api, thread["id"], "Explain Raft").text)
    assert resent[-1][0] == "done"
    messages = api.get(f"/api/tutor/threads/{thread['id']}/messages").json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "Explain Raft"),
        ("assistant", "Raft elects a leader."),
    ]
