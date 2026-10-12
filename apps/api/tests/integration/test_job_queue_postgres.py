import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.schema import Job
from app.services import job_queue, jobs

type Factory = async_sessionmaker[AsyncSession]

LIMITS = {"video": 1, "figure": 1}


async def seed(factory: Factory, *types: str) -> list[Job]:
    async with factory() as session:
        now = jobs.utcnow() - timedelta(seconds=10)
        created = [
            await jobs.enqueue(session, t, {}, now=now + timedelta(milliseconds=i))
            for i, t in enumerate(types)
        ]
        await session.commit()
        return created


async def claim(factory: Factory, worker: str, total: int = 2) -> Job | None:
    async with factory() as session:
        job = await job_queue.claim_next(
            session, worker, jobs.utcnow(), total_limit=total, type_limits=LIMITS, gates={}
        )
        await session.commit()
        return job


async def test_claim_skips_row_locked_by_another_transaction(pg_factory: Factory) -> None:
    first, second = await seed(pg_factory, "noop", "noop")
    async with pg_factory() as holder:
        # Another transaction (an in-flight cancel, say) holds the oldest row.
        await holder.execute(select(Job).where(Job.id == first.id).with_for_update())
        claimed = await asyncio.wait_for(claim(pg_factory, "w1"), timeout=5)
        assert claimed is not None and claimed.id == second.id
        await holder.rollback()
    claimed = await claim(pg_factory, "w2")
    assert claimed is not None and claimed.id == first.id


async def test_concurrent_claimers_respect_instance_limit(pg_factory: Factory) -> None:
    await seed(pg_factory, *["noop"] * 6)
    results = await asyncio.gather(*(claim(pg_factory, f"w{i}") for i in range(6)))
    claimed = [job for job in results if job is not None]
    assert len(claimed) == 2
    assert len({job.id for job in claimed}) == 2
    async with pg_factory() as session:
        running = (await session.execute(select(Job).where(Job.status == "running"))).all()
    assert len(running) == 2


async def test_concurrent_claimers_respect_render_limit(pg_factory: Factory) -> None:
    await seed(pg_factory, "video", "video", "noop")
    results = await asyncio.gather(*(claim(pg_factory, f"w{i}", total=3) for i in range(3)))
    types = sorted(job.type for job in results if job is not None)
    assert types == ["noop", "video"]
