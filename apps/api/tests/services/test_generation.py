import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.visual_seed import SeededTopic, seed_topic

from app.db.schema import (
    Asset,
    InstanceSettings,
    Job,
    PlanItem,
    Profile,
    StudyPlan,
    Topic,
    TopicRevision,
)
from app.services import budget, generation, providers
from app.services.budget import BudgetExhausted
from app.services.providers import NoProvider
from app.services.storyboard import TopicNotFound, TopicNotGrounded


@pytest.fixture(autouse=True)
def provider_checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    checked: list[str] = []

    async def ensure(session: AsyncSession, task_class: str, runtime: Any = None) -> None:
        checked.append(task_class)

    monkeypatch.setattr(providers, "ensure_provider_for", ensure)
    return checked


async def job_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(Job))).scalar_one())


async def test_one_job_per_format_with_dedupe_key(
    session: AsyncSession, provider_checks: list[str]
) -> None:
    seeded = await seed_topic(session)
    requested = await generation.request_for_topic(session, seeded.topic_id, ["figure", "video"])
    await session.commit()
    assert [(r.format, r.deduplicated) for r in requested] == [("video", False), ("figure", False)]
    video = requested[0].job
    assert video is not None and video.type == "video" and video.status == "queued"
    assert video.dedupe_key == f"video:{seeded.topic_id}"
    assert video.payload == {"topic_id": str(seeded.topic_id), "format": "video"}
    assert provider_checks == [
        "storyboarding",
        "script-generation",
        "script-repair",
        "figure-building",
    ]
    assert (await budget.budget_status(session)).jobs_today == 2


async def test_duplicate_request_returns_the_existing_job(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    (first,) = await generation.request_for_topic(session, seeded.topic_id, ["video"])
    await session.commit()
    (again,) = await generation.request_for_topic(session, seeded.topic_id, ["video"])
    assert again.deduplicated and again.job is not None and first.job is not None
    assert again.job.id == first.job.id
    assert await job_count(session) == 1
    assert (await budget.budget_status(session)).jobs_today == 1
    # Once the job finishes the key is free again.
    first.job.status = "ready"
    await session.commit()
    (third,) = await generation.request_for_topic(session, seeded.topic_id, ["video"])
    assert not third.deduplicated and third.job is not None and third.job.id != first.job.id


async def test_budget_exhaustion_rejects_without_creating_jobs(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    session.add(InstanceSettings(id=1, generation_jobs_day=1))
    await session.commit()
    with pytest.raises(BudgetExhausted, match="needs 2"):
        await generation.request_for_topic(session, seeded.topic_id, ["video", "figure"])
    assert await job_count(session) == 0
    await budget.record_render(session, 600 * 60)
    with pytest.raises(BudgetExhausted, match="render budget is used up"):
        await generation.request_for_topic(session, seeded.topic_id, ["video"])


async def test_missing_provider_and_ungrounded_topic_are_rejected(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_topic(session)

    async def none(session: AsyncSession, task_class: str, runtime: Any = None) -> None:
        raise NoProvider(f"No connected provider can run {task_class}.")

    monkeypatch.setattr(providers, "ensure_provider_for", none)
    with pytest.raises(NoProvider, match="storyboarding"):
        await generation.request_for_topic(session, seeded.topic_id, ["video"])
    bare = await seed_topic(session, chunks=(), profile=False)
    with pytest.raises(TopicNotGrounded):
        await generation.request_for_topic(session, bare.topic_id, ["video"])
    with pytest.raises(TopicNotFound):
        await generation.request_for_topic(session, uuid.uuid4(), ["video"])


async def test_merged_topic_generates_for_the_survivor(session: AsyncSession) -> None:
    survivor = await seed_topic(session)
    loser = await seed_topic(session, title="Leader election", profile=False)
    topic = await session.get(Topic, loser.topic_id)
    assert topic is not None
    topic.merged_into = survivor.topic_id
    await session.commit()
    (requested,) = await generation.request_for_topic(session, loser.topic_id, ["video"])
    assert requested.topic_id == survivor.topic_id


async def test_regenerate_carries_feedback_and_previous_asset(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    asset = Asset(topic_id=seeded.topic_id, format="figure", files={}, provenance={})
    session.add(asset)
    await session.commit()
    requested = await generation.regenerate(session, asset.id, "  bigger dial  ")
    assert requested.job is not None and requested.job.type == "figure"
    assert requested.job.payload == {
        "topic_id": str(seeded.topic_id),
        "format": "figure",
        "feedback": "bigger dial",
        "previous_asset_id": str(asset.id),
    }
    with pytest.raises(generation.AssetNotFound):
        await generation.regenerate(session, uuid.uuid4(), "x")


async def seed_plan(
    session: AsyncSession, topics: list[SeededTopic], *, excluded: int = -1
) -> None:
    plan = StudyPlan(version=1, created_by="run", profile_version=1)
    session.add(plan)
    await session.flush()
    for position, seeded in enumerate(topics):
        revision = (
            await session.execute(
                select(TopicRevision).where(TopicRevision.topic_id == seeded.topic_id)
            )
        ).scalar_one()
        session.add(
            PlanItem(
                plan_id=plan.id,
                topic_id=seeded.topic_id,
                topic_revision_id=revision.id,
                position=position,
                excluded=position == excluded,
            )
        )
    await session.commit()


async def test_plan_dry_run_counts_then_queues_in_plan_order(session: AsyncSession) -> None:
    first = await seed_topic(session, title="Terms")
    second = await seed_topic(session, title="Raft", profile=False)
    third = await seed_topic(session, title="Excluded", profile=False)
    await seed_plan(session, [first, second, third], excluded=2)
    (existing,) = await generation.request_for_topic(session, second.topic_id, ["figure"])
    await session.commit()

    preview = await generation.request_for_plan(session, dry_run=True)
    assert preview.plan_version == 1 and preview.formats == ["video", "figure"]
    assert [(preview.titles[i.topic_id], i.format, i.deduplicated) for i in preview.items] == [
        ("Terms", "video", False),
        ("Terms", "figure", False),
        ("Raft", "video", False),
        ("Raft", "figure", True),
    ]
    assert preview.new_jobs == 3 and await job_count(session) == 1

    queued = await generation.request_for_plan(session, dry_run=False)
    await session.commit()
    assert queued.new_jobs == 3
    created = [i.job for i in queued.items if not i.deduplicated]
    assert [(j.type, j.payload["topic_id"]) for j in created if j is not None] == [
        ("video", str(first.topic_id)),
        ("figure", str(first.topic_id)),
        ("video", str(second.topic_id)),
    ]
    assert existing.job is not None and queued.items[3].job is not None
    assert queued.items[3].job.id == existing.job.id
    assert (await budget.budget_status(session)).jobs_today == 4


async def test_plan_uses_preferred_formats_and_is_all_or_nothing(session: AsyncSession) -> None:
    first = await seed_topic(session)
    await seed_plan(session, [first])
    profile = (await session.execute(select(Profile))).scalar_one()
    profile.preferred_formats = ["text", "video"]
    session.add(InstanceSettings(id=1, generation_jobs_day=0))
    await session.commit()
    preview = await generation.request_for_plan(session, dry_run=True)
    assert preview.formats == ["video"] and preview.new_jobs == 1
    with pytest.raises(BudgetExhausted):
        await generation.request_for_plan(session, dry_run=False)
    assert await job_count(session) == 0
    profile.preferred_formats = ["text"]
    await session.commit()
    with pytest.raises(generation.NothingToGenerate):
        await generation.request_for_plan(session, dry_run=True)


async def test_no_plan_is_not_found(session: AsyncSession) -> None:
    with pytest.raises(generation.NoPlan):
        await generation.request_for_plan(session, dry_run=True)
