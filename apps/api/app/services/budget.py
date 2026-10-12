"""Generation budgets in ``usage_counters`` (design D7 "Budget", task 7.5).

``render_minutes_month`` counts sandbox wall-clock minutes per UTC month (period ``YYYY-MM``).
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import UsageCounter

log = logging.getLogger(__name__)

RENDER_MINUTES = "render_minutes_month"


def utcnow() -> datetime:
    return datetime.now(UTC)


def month_period(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m")


async def counter(session: AsyncSession, name: str, period: str) -> UsageCounter:
    """The counter row for ``(name, period)``, created at zero (flushed, not committed)."""
    stmt = select(UsageCounter).where(UsageCounter.counter == name, UsageCounter.period == period)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = UsageCounter(counter=name, period=period, value=0)
        session.add(row)
        await session.flush()
    return row


async def record_render(
    session: AsyncSession, seconds: float, *, now: datetime | None = None
) -> float:
    """Add a sandbox run's wall-clock time to this month's render minutes; returns the total."""
    row = await counter(session, RENDER_MINUTES, month_period(now or utcnow()))
    row.value = float(row.value) + max(seconds, 0.0) / 60.0
    await session.flush()
    log.info("render minutes this month: %.2f", row.value)
    return row.value
