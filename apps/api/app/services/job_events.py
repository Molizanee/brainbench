"""Job events: one ``job_events`` row per status change and progress update, pushed to
listeners through Postgres ``LISTEN/NOTIFY``.

``record_event`` inserts the row and issues ``pg_notify`` in the caller's transaction, so a
notification is delivered only when the change commits. The notification carries ids only
(``{"job_id", "event_id"}``); listeners load the row, which keeps payloads far below the 8 kB
NOTIFY limit. ``EventHub`` holds one ``LISTEN`` connection per process and fans notifications
out to subscribers (the ``/events`` streams).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.db.schema import Job, JobEvent

log = logging.getLogger(__name__)

CHANNEL = "brainbench_jobs"
EVENT_KINDS = ("status", "progress")
SUBSCRIBER_QUEUE_SIZE = 1000


@dataclass(frozen=True)
class Notification:
    job_id: UUID
    event_id: int


async def record_event(
    session: AsyncSession,
    job: Job,
    kind: str,
    *,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> JobEvent:
    """Insert an event for ``job`` and notify listeners on commit (flushed, not committed)."""
    event = JobEvent(job_id=job.id, kind=kind, status=job.status, message=message, data=data or {})
    session.add(event)
    await session.flush()
    if session.get_bind().dialect.name == "postgresql":
        payload = json.dumps({"job_id": str(job.id), "event_id": event.id})
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload}
        )
    return event


def parse_notification(payload: str) -> Notification | None:
    try:
        data = json.loads(payload)
        return Notification(job_id=UUID(data["job_id"]), event_id=int(data["event_id"]))
    except ValueError, KeyError, TypeError:
        log.warning("ignoring malformed job notification %r", payload)
        return None


async def load_notification(
    session: AsyncSession, notification: Notification
) -> tuple[Job, JobEvent] | None:
    """The job and event a notification names, or None when either is gone."""
    job = await session.get(Job, notification.job_id)
    event = (
        await session.execute(select(JobEvent).where(JobEvent.id == notification.event_id))
    ).scalar_one_or_none()
    if job is None or event is None:
        return None
    return job, event


type Subscription = asyncio.Queue[Notification | None]


class EventHub:
    """One ``LISTEN`` connection shared by every subscriber of this process.

    The connection opens with the first subscriber and closes with the last. A subscriber's
    queue receives ``None`` when the connection drops; the SSE stream then ends and the browser's
    ``EventSource`` reconnects, which opens a fresh connection.
    """

    def __init__(self, engine: AsyncEngine, channel: str = CHANNEL) -> None:
        self.engine = engine
        self.channel = channel
        self._subscribers: set[Subscription] = set()
        self._connection: AsyncConnection | None = None
        self._driver: Any = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        queue: Subscription = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        async with self._lock:
            if self._connection is None:
                await self._listen()
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
                if not self._subscribers:
                    await self._close()

    async def _listen(self) -> None:
        connection = await self.engine.connect()
        try:
            raw = await connection.get_raw_connection()
            driver: Any = raw.driver_connection  # asyncpg.Connection (asyncpg ships no types)
            await driver.add_listener(self.channel, self._on_notify)
            driver.add_termination_listener(self._on_terminate)
        except BaseException:
            await connection.close()
            raise
        self._connection, self._driver = connection, driver
        log.info("listening on %s", self.channel)

    async def _close(self) -> None:
        connection, driver = self._connection, self._driver
        self._connection = self._driver = None
        if connection is None:
            return
        try:
            if not driver.is_closed():
                await driver.remove_listener(self.channel, self._on_notify)
        finally:
            await connection.close()

    def _on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        notification = parse_notification(payload)
        if notification is not None:
            self.publish(notification)

    def _on_terminate(self, _connection: object) -> None:
        log.warning("job event listener connection closed")
        self._connection = self._driver = None
        for queue in list(self._subscribers):
            _force_put(queue, None)

    def publish(self, notification: Notification) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(notification)
            except asyncio.QueueFull:
                log.warning("event subscriber is not keeping up; dropping job event")


def _force_put(queue: Subscription, item: Notification | None) -> None:
    while True:
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            queue.get_nowait()
