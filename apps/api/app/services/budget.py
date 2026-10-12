"""Generation budgets in ``usage_counters`` (design D7 "Budget", task 7.5).

- ``render_minutes_month``: sandbox wall-clock minutes per UTC month (period ``YYYY-MM``), added
  after every render or Node run. When the month's minutes reach
  ``settings.render_minutes_month``, new generation requests are rejected and queued video and
  figure jobs are not started (claim gate) until the month resets.
- ``generation_jobs_day``: generation jobs accepted per UTC day (period ``YYYY-MM-DD``), added
  at enqueue. A request that would exceed ``settings.generation_jobs_day`` is rejected.

The limits are the instance ``settings`` row (defaults 600 minutes and 20 jobs).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.db.schema import InstanceSettings, UsageCounter

log = logging.getLogger(__name__)

RENDER_MINUTES = "render_minutes_month"
GENERATION_JOBS = "generation_jobs_day"
DEFAULT_RENDER_MINUTES = 600
DEFAULT_GENERATION_JOBS = 20


class BudgetExhausted(Conflict):
    code = "budget-exhausted"


def utcnow() -> datetime:
    return datetime.now(UTC)


def month_period(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m")


def day_period(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%d")


def next_month(now: datetime) -> datetime:
    current = now.astimezone(UTC)
    year, month = (
        (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    )
    return datetime(year, month, 1, tzinfo=UTC)


def next_day(now: datetime) -> datetime:
    current = now.astimezone(UTC)
    return datetime(current.year, current.month, current.day, tzinfo=UTC) + timedelta(days=1)


async def counter(session: AsyncSession, name: str, period: str) -> UsageCounter:
    """The counter row for ``(name, period)``, created at zero (flushed, not committed)."""
    stmt = select(UsageCounter).where(UsageCounter.counter == name, UsageCounter.period == period)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = UsageCounter(counter=name, period=period, value=0)
        session.add(row)
        await session.flush()
    return row


async def counter_value(session: AsyncSession, name: str, period: str) -> float:
    stmt = select(UsageCounter.value).where(
        UsageCounter.counter == name, UsageCounter.period == period
    )
    return float((await session.execute(stmt)).scalar_one_or_none() or 0)


async def limits(session: AsyncSession) -> tuple[int, int]:
    """``(render minutes per month, generation jobs per day)`` without writing anything."""
    row = await session.get(InstanceSettings, 1)
    if row is None:
        return DEFAULT_RENDER_MINUTES, DEFAULT_GENERATION_JOBS
    return row.render_minutes_month, row.generation_jobs_day


@dataclass(frozen=True)
class BudgetStatus:
    render_minutes_used: float
    render_minutes_limit: int
    render_resets_at: datetime
    jobs_today: int
    jobs_limit: int
    jobs_reset_at: datetime

    @property
    def render_minutes_left(self) -> float:
        return max(self.render_minutes_limit - self.render_minutes_used, 0.0)

    @property
    def jobs_left(self) -> int:
        return max(self.jobs_limit - self.jobs_today, 0)

    @property
    def render_exhausted(self) -> bool:
        return self.render_minutes_used >= self.render_minutes_limit


async def budget_status(session: AsyncSession, *, now: datetime | None = None) -> BudgetStatus:
    now = now or utcnow()
    render_limit, jobs_limit = await limits(session)
    return BudgetStatus(
        render_minutes_used=round(
            await counter_value(session, RENDER_MINUTES, month_period(now)), 2
        ),
        render_minutes_limit=render_limit,
        render_resets_at=next_month(now),
        jobs_today=int(await counter_value(session, GENERATION_JOBS, day_period(now))),
        jobs_limit=jobs_limit,
        jobs_reset_at=next_day(now),
    )


async def check_can_enqueue(
    session: AsyncSession, count: int, *, now: datetime | None = None
) -> BudgetStatus:
    """Raise :class:`BudgetExhausted` (409) when ``count`` new generation jobs do not fit."""
    status = await budget_status(session, now=now)
    if count <= 0:
        return status
    if status.render_exhausted:
        raise BudgetExhausted(
            f"The monthly render budget is used up ({status.render_minutes_used:g} of "
            f"{status.render_minutes_limit} minutes); it resets on "
            f"{status.render_resets_at:%Y-%m-%d}."
        )
    if status.jobs_today + count > status.jobs_limit:
        raise BudgetExhausted(
            f"The daily generation cap allows {status.jobs_left} more job(s) today "
            f"({status.jobs_today} of {status.jobs_limit} used) and this request needs {count}; "
            f"it resets on {status.jobs_reset_at:%Y-%m-%d %H:%M} UTC."
        )
    return status


async def record_generation_jobs(
    session: AsyncSession, count: int, *, now: datetime | None = None
) -> None:
    row = await counter(session, GENERATION_JOBS, day_period(now or utcnow()))
    row.value = float(row.value) + count
    await session.flush()


async def record_render(
    session: AsyncSession, seconds: float, *, now: datetime | None = None
) -> float:
    """Add a sandbox run's wall-clock time to this month's render minutes; returns the total."""
    row = await counter(session, RENDER_MINUTES, month_period(now or utcnow()))
    row.value = float(row.value) + max(seconds, 0.0) / 60.0
    await session.flush()
    log.info("render minutes this month: %.2f", row.value)
    return row.value


async def claim_allowed(session: AsyncSession, *, now: datetime | None = None) -> bool:
    """Claim gate for ``video`` and ``figure``: no new render while the month's minutes are used
    up, or while today's accepted jobs exceed a cap that was lowered after they were queued."""
    status = await budget_status(session, now=now)
    allowed = not status.render_exhausted and status.jobs_today <= status.jobs_limit
    if not allowed:
        log.info("render jobs held by the budget: %s", status)
    return allowed
