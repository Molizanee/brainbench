"""Budget claim gates and request dedupe on Postgres (skipped without a Postgres DATABASE_URL).

The fixtures copy ``tests/integration/jobs/conftest.py``: each test gets a throwaway schema.
"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.visual_seed import seed_topic

from app.db.schema import Base, InstanceSettings
from app.jobs.registry import CLAIM_GATES
from app.services import budget, generation, job_queue, jobs, providers

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("needs DATABASE_URL pointing at Postgres")
    schema = f"it_{uuid4().hex[:12]}"
    admin = create_async_engine(DATABASE_URL)
    async with admin.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        DATABASE_URL, execution_options={"schema_translate_map": {None: schema}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture(autouse=True)
def provider_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ensure(session: AsyncSession, task_class: str, runtime: Any = None) -> None:
        return None

    monkeypatch.setattr(providers, "ensure_provider_for", ensure)


async def test_exhausted_render_budget_holds_render_jobs_at_claim(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_factory() as session:
        seeded = await seed_topic(session)
        (video,) = await generation.request_for_topic(session, seeded.topic_id, ["video"])
        noop = await jobs.enqueue(session, "noop", {})
        session.add(InstanceSettings(id=1, render_minutes_month=5))
        await budget.record_render(session, 5 * 60)
        await session.commit()
    async with pg_factory() as session:
        claimed = await job_queue.claim_next(
            session,
            "w",
            datetime.now(UTC),
            total_limit=2,
            type_limits=job_queue.render_type_limits(),
            gates=CLAIM_GATES,
        )
        await session.commit()
    assert claimed is not None and claimed.id == noop.id
    async with pg_factory() as session:
        assert video.job is not None
        nothing = await job_queue.claim_next(
            session,
            "w",
            datetime.now(UTC),
            total_limit=2,
            type_limits=job_queue.render_type_limits(),
            gates=CLAIM_GATES,
        )
        assert nothing is None
        row = await session.get(InstanceSettings, 1)
        assert row is not None
        row.render_minutes_month = 600
        await session.commit()
    async with pg_factory() as session:
        claimed = await job_queue.claim_next(
            session,
            "w",
            datetime.now(UTC),
            total_limit=2,
            type_limits=job_queue.render_type_limits(),
            gates=CLAIM_GATES,
        )
        await session.commit()
    assert claimed is not None and claimed.id == video.job.id


async def test_duplicate_requests_share_one_job(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_factory() as session:
        seeded = await seed_topic(session)
    ids = []
    for _ in range(2):
        async with pg_factory() as session:
            (requested,) = await generation.request_for_topic(session, seeded.topic_id, ["figure"])
            await session.commit()
            assert requested.job is not None
            ids.append((requested.job.id, requested.deduplicated))
    assert ids[0][0] == ids[1][0] and ids[1][1] is True
    async with pg_factory() as session:
        assert (await budget.budget_status(session)).jobs_today == 1
