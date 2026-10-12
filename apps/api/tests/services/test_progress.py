import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import StudySession, Topic
from app.services.progress import (
    mastered_topic_ids,
    session_history,
    status_for,
    topic_stats,
    topic_status,
    used_pct,
    week_bounds,
)

T0 = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)  # a Monday


async def add_topic(session: AsyncSession, *ratings: str, minutes: int = 30) -> uuid.UUID:
    topic = Topic()
    session.add(topic)
    await session.flush()
    for i, rating in enumerate(ratings):
        session.add(
            StudySession(
                topic_id=topic.id,
                minutes=minutes,
                rating=rating,
                studied_at=T0 + timedelta(hours=i),
            )
        )
    await session.commit()
    return topic.id


@pytest.mark.parametrize(
    ("latest", "status"),
    [
        (None, "not-started"),
        ("struggled", "in-progress"),
        ("understood", "in-progress"),
        ("confident", "mastered"),
        ("could-teach", "mastered"),
    ],
)
def test_status_for_latest_rating(latest: str | None, status: str) -> None:
    assert status_for(latest) == status


async def test_topic_status_uses_latest_session(session: AsyncSession) -> None:
    assert await topic_status(session, await add_topic(session)) == "not-started"
    assert await topic_status(session, await add_topic(session, "confident")) == "mastered"
    assert (
        await topic_status(session, await add_topic(session, "struggled", "confident"))
        == "mastered"
    )


async def test_session_history_and_mastered_ids(session: AsyncSession) -> None:
    mastered = await add_topic(session, "understood", "could-teach")
    learning = await add_topic(session, "struggled")
    untouched = await add_topic(session)

    history = await session_history(session, [mastered, learning, untouched])
    assert [s.rating for s in history[mastered]] == ["understood", "could-teach"]
    assert [s.rating for s in history[learning]] == ["struggled"]
    assert history[untouched] == []
    assert await session_history(session, []) == {}
    assert await mastered_topic_ids(session) == {mastered}


async def test_topic_stats_totals(session: AsyncSession) -> None:
    topic = await add_topic(session, "struggled", "understood", minutes=25)
    other = uuid.uuid4()
    stats = await topic_stats(session, [topic, other])
    assert (stats[topic].status, stats[topic].minutes, stats[topic].sessions) == (
        "in-progress",
        50,
        2,
    )
    assert stats[other].status == "not-started"


@pytest.mark.parametrize(
    ("now", "offset", "start"),
    [
        # Wednesday noon UTC: the week starts Monday 00:00 UTC.
        (datetime(2026, 10, 14, 12, tzinfo=UTC), 0, datetime(2026, 10, 12, tzinfo=UTC)),
        # Monday 01:00 UTC is still Sunday evening in São Paulo (UTC-3): previous week.
        (datetime(2026, 10, 12, 1, tzinfo=UTC), -180, datetime(2026, 10, 5, 3, tzinfo=UTC)),
        # Sunday 23:00 UTC is already Monday in Tokyo (UTC+9).
        (datetime(2026, 10, 11, 23, tzinfo=UTC), 540, datetime(2026, 10, 11, 15, tzinfo=UTC)),
    ],
)
def test_week_bounds(now: datetime, offset: int, start: datetime) -> None:
    assert week_bounds(now, offset) == (start, start + timedelta(days=7))


def test_used_pct() -> None:
    assert used_pct(180, 300) == 60.0
    assert used_pct(0, 300) == 0.0
    assert used_pct(400, 300) == 133.3
    assert used_pct(10, None) is None
