import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.services import jobs
from app.services.job_events import EventHub, load_notification

type Factory = async_sessionmaker[AsyncSession]


async def test_notification_arrives_only_after_commit(
    pg_engine: AsyncEngine, pg_factory: Factory
) -> None:
    hub = EventHub(pg_engine)
    async with hub.subscribe() as queue, pg_factory() as session:
        job = await jobs.enqueue(session, "noop", {})
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), 0.3)
        await session.commit()
        notification = await asyncio.wait_for(queue.get(), 5)

    assert notification is not None and notification.job_id == job.id
    async with pg_factory() as session:
        loaded = await load_notification(session, notification)
    assert loaded is not None
    loaded_job, event = loaded
    assert (loaded_job.id, event.kind, event.status) == (job.id, "status", "queued")


async def test_rolled_back_change_is_never_announced(
    pg_engine: AsyncEngine, pg_factory: Factory
) -> None:
    hub = EventHub(pg_engine)
    async with hub.subscribe() as queue:
        async with pg_factory() as session:
            await jobs.enqueue(session, "noop", {})
            await session.rollback()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), 0.3)


async def test_every_subscriber_gets_each_event_and_last_one_closes_listener(
    pg_engine: AsyncEngine, pg_factory: Factory
) -> None:
    hub = EventHub(pg_engine)
    async with hub.subscribe() as first, hub.subscribe() as second:
        async with pg_factory() as session:
            job = await jobs.enqueue(session, "noop", {})
            await jobs.cancel(session, job.id)
            await session.commit()
        for queue in (first, second):
            got = [await asyncio.wait_for(queue.get(), 5) for _ in range(2)]
            assert [n.job_id for n in got if n] == [job.id, job.id]
    assert hub._connection is None
