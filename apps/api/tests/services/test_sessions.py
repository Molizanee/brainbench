import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import StudySession, Topic
from app.models.progress import StudySessionCreate
from app.services.progress import topic_status
from app.services.sessions import SessionService
from app.services.topics import TopicNotFound

NOW = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)


async def test_log_stores_session_with_clock_time(session: AsyncSession) -> None:
    topic = Topic()
    session.add(topic)
    await session.commit()

    logged = await SessionService(session, now=lambda: NOW).log(
        topic.id, StudySessionCreate(minutes=45, rating="understood", note="   ")
    )

    assert logged.session.studied_at == NOW
    assert logged.session.note is None
    assert logged.topic.minutes_studied == 45
    stored = await session.get(StudySession, logged.session.id)
    assert stored is not None
    assert stored.topic_id == topic.id


async def test_missing_topic_raises(session: AsyncSession) -> None:
    with pytest.raises(TopicNotFound):
        await SessionService(session).log(
            uuid.uuid4(), StudySessionCreate(minutes=5, rating="struggled")
        )


async def test_merge_chain_follows_to_survivor(session: AsyncSession) -> None:
    a = Topic()
    session.add(a)
    await session.flush()
    b = Topic(merged_into=a.id)
    session.add(b)
    await session.flush()
    c = Topic(merged_into=b.id)
    session.add(c)
    await session.commit()

    logged = await SessionService(session).log(
        c.id, StudySessionCreate(minutes=5, rating="struggled")
    )
    assert logged.topic.topic_id == a.id


async def test_merge_cycle_is_not_followed_forever(session: AsyncSession) -> None:
    a, b = Topic(), Topic()
    session.add_all([a, b])
    await session.flush()
    a.merged_into, b.merged_into = b.id, a.id
    await session.commit()

    with pytest.raises(TopicNotFound):
        await SessionService(session).log(a.id, StudySessionCreate(minutes=5, rating="struggled"))


class Ticking:
    """A clock that advances one minute per call, so each session is strictly later."""

    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        self.t += timedelta(minutes=1)
        return self.t


async def test_status_transitions_including_regression_after_mastery(
    session: AsyncSession,
) -> None:
    topic = Topic()
    session.add(topic)
    await session.commit()
    service = SessionService(session, now=Ticking())
    assert await topic_status(session, topic.id) == "not-started"

    steps = [
        ("understood", 45, "in-progress", 45),
        ("could-teach", 30, "mastered", 75),
        ("struggled", 20, "in-progress", 95),
        ("confident", 10, "mastered", 105),
    ]
    for rating, minutes, status, total in steps:
        logged = await service.log(topic.id, StudySessionCreate(minutes=minutes, rating=rating))
        assert (logged.topic.status, logged.topic.minutes_studied) == (status, total)
        assert await topic_status(session, topic.id) == status
