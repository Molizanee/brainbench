"""The curation job and plan edits against Postgres with pgvector (``DATABASE_URL``).

Runs the real worker path: ``start_run`` enqueues, a ``Runner`` claims and runs the handler
with the scripted model and fake embeddings, checkpoints land in ``jobs.checkpoint`` (JSONB), and
revision embeddings in a ``vector(384)`` column. Each test gets its own schema.
"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.support.curation import (
    Script,
    install_fake_embeddings,
    install_script,
    seed_profile,
    seed_source,
)

from app.core.config import settings
from app.db.schema import Base, Job, StudyPlan, TopicRevision
from app.jobs.registry import HANDLERS
from app.jobs.runner import Runner
from app.models.plans import IncludeEdit, MergeEdit, RemoveEdit, ReorderEdit, TopicEdit
from app.services import providers
from app.services.curation import start_run
from app.services.plans import PlanService, PrerequisiteViolation
from brainbench_providers.select import ALL_ACCOUNTS
from brainbench_providers.strands_models import ProviderThrottledException

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
        reason="needs DATABASE_URL (Postgres)",
    ),
]


@pytest.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ["DATABASE_URL"]
    schema = f"test_curation_{uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
        execution_options={"schema_translate_map": {None: schema}},
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


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Script:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    install_fake_embeddings(monkeypatch)

    async def ensure(session: AsyncSession, task_class: str) -> None:
        return None

    monkeypatch.setattr(providers, "ensure_provider_for", ensure)
    script = Script(
        relevance={"CSS": 0.2},
        estimates={"Snapshots": 450},
        edges=[("Logs", "Terms", 0.9), ("Snapshots", "Logs", 0.8), ("Terms", "Snapshots", 0.1)],
    )
    install_script(monkeypatch, script)
    return script


async def seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await seed_profile(session, budget_hours=5)
        await seed_source(
            session,
            "Raft paper",
            ["Terms: Leaders.", "Logs: Replication.", "Snapshots: Compaction."],
        )
        await seed_source(session, "Web notes", ["CSS: Layout."])


async def run_job(factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> Job:
    async with factory() as session:
        _, job = await start_run(session)
        await session.commit()
        job_id = job.id
    runner = Runner(factory, worker_id="w1", handlers=HANDLERS, gates={}, jobs_dir=tmp_path)
    (active,) = await runner.tick()
    assert active.task is not None
    await active.task
    async with factory() as session:
        finished = await session.get(Job, job_id)
        assert finished is not None
        return finished


async def test_worker_runs_curation_end_to_end(
    pg_factory: async_sessionmaker[AsyncSession], script: Script, tmp_path: Path
) -> None:
    await seed(pg_factory)

    job = await run_job(pg_factory, tmp_path)

    assert (job.status, job.attempt) == ("ready", 1), job.error
    assert {"inputs", "windows", "candidates", "topics", "scores", "edges"} <= set(job.checkpoint)
    async with pg_factory() as session:
        plan = await PlanService(session).current()
        embeddings = list(await session.scalars(select(TopicRevision.embedding)))
    assert [i.title for i in plan.items] == ["Terms", "Logs", "Snapshots"]
    assert [i.title for i in plan.excluded] == ["CSS"]
    assert [(w.week_start, w.week_span, w.exceeds_weekly_budget) for w in plan.weeks] == [
        (1, 1, False),
        (2, 2, True),
    ]
    assert all(e is not None and len(e) == 384 for e in embeddings)
    assert plan.run is not None and plan.run.models["scoring"].model == "scripted-scoring"
    assert not plan.stale


async def test_provider_limit_defers_and_resumes_from_the_checkpoint(
    pg_factory: async_sessionmaker[AsyncSession],
    script: Script,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed(pg_factory)
    throttled = {"done": False}

    def scoring_limited(call):  # type: ignore[no-untyped-def]
        if "TopicScoring" in call.tool_names and not throttled["done"]:
            throttled["done"] = True
            return ProviderThrottledException(
                "limit", reset_at=datetime.now(UTC) + timedelta(hours=1), scope=ALL_ACCOUNTS
            )
        return script(call)

    model = install_script(monkeypatch, script)
    model.replies = scoring_limited

    deferred = await run_job(pg_factory, tmp_path)
    assert (deferred.status, deferred.attempt) == ("queued", 1)
    assert deferred.checkpoint["topics"] and not deferred.checkpoint.get("scores")
    extraction_calls = script.steps.count("topic_extraction")

    async with pg_factory() as session:
        await session.execute(
            update(Job).where(Job.id == deferred.id).values(run_after=datetime.now(UTC))
        )
        await session.commit()
    runner = Runner(pg_factory, worker_id="w2", handlers=HANDLERS, gates={}, jobs_dir=tmp_path)
    (active,) = await runner.tick()
    assert active.task is not None
    await active.task

    async with pg_factory() as session:
        job = await session.get(Job, deferred.id)
        assert job is not None and job.status == "ready" and job.attempt == 1
        assert script.steps.count("topic_extraction") == extraction_calls
        assert await session.scalar(select(StudyPlan.version)) == 1


async def test_plan_edits_write_versions_on_postgres(
    pg_factory: async_sessionmaker[AsyncSession], script: Script, tmp_path: Path
) -> None:
    await seed(pg_factory)
    await run_job(pg_factory, tmp_path)

    async with pg_factory() as session:
        plans = PlanService(session)
        v1 = await plans.current()
        ids = {i.title: i.topic_id for i in [*v1.items, *v1.excluded]}
        with pytest.raises(PrerequisiteViolation):
            await plans.edit(
                1, ReorderEdit(op="reorder", order=[ids["Logs"], ids["Terms"], ids["Snapshots"]])
            )
        v2 = await plans.edit(1, IncludeEdit(op="include", topic_id=ids["CSS"]))
        v3 = await plans.edit(
            2, TopicEdit(op="edit", topic_id=ids["Logs"], title="Log replication")
        )
        v4 = await plans.edit(
            3, MergeEdit(op="merge", topic_id=ids["CSS"], into_topic_id=ids["Terms"])
        )
        v5 = await plans.edit(4, RemoveEdit(op="remove", topic_id=ids["Snapshots"]))
        again = await plans.get(1)

    assert [p.version for p in (v2, v3, v4, v5)] == [2, 3, 4, 5]
    assert next(i for i in v2.items if i.title == "CSS").manually_included
    assert "Log replication" in [i.title for i in v3.items]
    assert "CSS" not in [i.title for i in v4.items]
    assert [i.title for i in v5.items] == ["Terms", "Log replication"]
    assert [i.title for i in again.items] == ["Terms", "Logs", "Snapshots"]
