from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import InstanceSettings
from app.jobs.registry import CLAIM_GATES
from app.services import budget
from app.services.budget import BudgetExhausted

NOW = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)


def test_periods_and_reset_dates() -> None:
    assert budget.month_period(NOW) == "2026-12" and budget.day_period(NOW) == "2026-12-31"
    assert budget.next_month(NOW) == datetime(2027, 1, 1, tzinfo=UTC)
    assert budget.next_day(NOW) == datetime(2027, 1, 1, tzinfo=UTC)
    assert budget.next_month(datetime(2026, 10, 12, tzinfo=UTC)) == datetime(
        2026, 11, 1, tzinfo=UTC
    )


async def test_status_defaults_and_counters(session: AsyncSession) -> None:
    status = await budget.budget_status(session, now=NOW)
    assert (status.render_minutes_limit, status.jobs_limit) == (600, 20)
    assert status.render_minutes_left == 600 and status.jobs_left == 20
    await budget.record_render(session, 90, now=NOW)
    await budget.record_render(session, 30, now=NOW)
    await budget.record_generation_jobs(session, 3, now=NOW)
    status = await budget.budget_status(session, now=NOW)
    assert status.render_minutes_used == 2.0 and status.jobs_today == 3
    # next month and next day start from zero
    later = await budget.budget_status(session, now=datetime(2027, 1, 1, 0, 1, tzinfo=UTC))
    assert later.render_minutes_used == 0 and later.jobs_today == 0


async def test_render_budget_exhaustion_rejects_enqueue_with_reset_date(
    session: AsyncSession,
) -> None:
    session.add(InstanceSettings(id=1, render_minutes_month=10, generation_jobs_day=20))
    await budget.record_render(session, 10 * 60, now=NOW)
    with pytest.raises(BudgetExhausted, match=r"10 of 10 minutes.*resets on 2027-01-01"):
        await budget.check_can_enqueue(session, 1, now=NOW)
    assert not await budget.claim_allowed(session, now=NOW)
    assert await budget.claim_allowed(session, now=datetime(2027, 1, 2, tzinfo=UTC))


async def test_daily_cap_counts_the_whole_request(session: AsyncSession) -> None:
    session.add(InstanceSettings(id=1, generation_jobs_day=5))
    await budget.record_generation_jobs(session, 4, now=NOW)
    await budget.check_can_enqueue(session, 1, now=NOW)
    with pytest.raises(BudgetExhausted, match=r"1 more job\(s\) today .* needs 2"):
        await budget.check_can_enqueue(session, 2, now=NOW)
    await budget.record_generation_jobs(session, 1, now=NOW)
    assert await budget.claim_allowed(session, now=NOW)  # queued jobs within the cap still run


async def test_claim_gates_are_registered_for_render_types(session: AsyncSession) -> None:
    assert {"video", "figure"} <= CLAIM_GATES.keys()
    assert await CLAIM_GATES["video"](session)
    session.add(InstanceSettings(id=1, render_minutes_month=1))
    await budget.record_render(session, 61)
    assert not await CLAIM_GATES["video"](session)
    assert not await CLAIM_GATES["figure"](session)
