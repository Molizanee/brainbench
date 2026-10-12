import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import events
from app.services import jobs
from app.services.job_events import Notification, Subscription


class FakeHub:
    """Stands in for the LISTEN connection: replays queued notifications, then ends."""

    def __init__(self, notifications: list[Notification | None]) -> None:
        self.notifications = notifications
        self.subscribed = 0
        self.released = 0

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        queue: Subscription = asyncio.Queue()
        for item in self.notifications:
            queue.put_nowait(item)
        self.subscribed += 1
        try:
            yield queue
        finally:
            self.released += 1


def use_hub(app: FastAPI, hub: FakeHub, factory: async_sessionmaker[AsyncSession]) -> None:
    app.dependency_overrides[events.get_event_hub] = lambda: hub
    app.dependency_overrides[events.get_event_sessions] = lambda: factory


def messages(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    client.post("/api/auth/setup", json={"password": "correct horse"})
    return client


def test_events_require_a_session(client: TestClient) -> None:
    assert client.get("/events").status_code == 401


async def test_job_event_is_streamed_as_updated_and_event(
    signed_in: TestClient,
    app: FastAPI,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await jobs.enqueue(session, "noop", {})
    await session.commit()
    (event,) = await jobs.list_events(session, job.id)
    hub = FakeHub([Notification(job.id, 999_999), Notification(job.id, event.id), None])
    use_hub(app, hub, session_factory)

    with signed_in.stream("GET", "/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    assert body.startswith("retry: 3000\n\n")
    received = messages(body)
    assert [m["type"] for m in received] == ["jobs.updated", "jobs.event"]
    assert received[0]["payload"]["id"] == str(job.id)
    assert received[0]["payload"]["status"] == "queued"
    assert received[1]["payload"] == {
        "id": event.id,
        "job_id": str(job.id),
        "kind": "status",
        "status": "queued",
        "message": "queued",
        "data": {},
        "created_at": received[1]["payload"]["created_at"],
    }
    assert hub.released == hub.subscribed == 1


async def test_idle_stream_sends_keepalive(
    signed_in: TestClient,
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "KEEPALIVE_S", 0.01)

    class OneTimeout(FakeHub):
        @asynccontextmanager
        async def subscribe(self) -> AsyncIterator[Subscription]:
            queue: Subscription = asyncio.Queue()

            async def close_later() -> None:
                await asyncio.sleep(0.05)
                queue.put_nowait(None)

            task = asyncio.create_task(close_later())
            try:
                yield queue
            finally:
                task.cancel()

    use_hub(app, OneTimeout([]), session_factory)
    with signed_in.stream("GET", "/events") as response:
        body = "".join(response.iter_text())
    assert ": keepalive\n\n" in body
