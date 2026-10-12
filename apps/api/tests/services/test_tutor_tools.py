import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.schema import (
    Asset,
    CurrentSkill,
    PlanItem,
    Profile,
    Source,
    StudyPlan,
    StudySession,
    TargetSkill,
    Topic,
    TopicRevision,
)
from app.services import search
from app.services.search import ChunkHit
from app.services.tutor_tools import (
    TopicNotFound,
    build_tutor_tools,
    load_context,
    plan_summary,
    resolve_topic,
)
from brainbench_agents.tutor import SkillLine


async def topic(session: AsyncSession, *titles: str) -> uuid.UUID:
    row = Topic()
    session.add(row)
    await session.flush()
    for number, title in enumerate(titles, start=1):
        session.add(
            TopicRevision(
                topic_id=row.id,
                revision=number,
                title=title,
                summary=f"{title} summary",
                created_by="run",
            )
        )
    await session.flush()
    return row.id


async def test_resolve_topic_follows_merges_and_rejects_cycles(session: AsyncSession) -> None:
    survivor = await topic(session, "Raft")
    merged = await topic(session, "Raft old")
    looped_a = await topic(session, "A")
    looped_b = await topic(session, "B")
    for source, target in ((merged, survivor), (looped_a, looped_b), (looped_b, looped_a)):
        row = await session.get(Topic, source)
        assert row is not None
        row.merged_into = target
    await session.flush()
    assert (await resolve_topic(session, merged)).id == survivor
    for missing in (looped_a, uuid.uuid4()):
        with pytest.raises(TopicNotFound):
            await resolve_topic(session, missing)


async def test_context_carries_profile_and_latest_revision(session: AsyncSession) -> None:
    profile = Profile(display_name="Ada", weekly_budget_hours=4, language="de", background="SRE")
    session.add(profile)
    await session.flush()
    session.add_all(
        [
            CurrentSkill(profile_id=profile.id, name="Go", name_normalized="go", level="expert"),
            TargetSkill(
                profile_id=profile.id,
                name="Consensus",
                name_normalized="consensus",
                target_level="proficient",
                priority="high",
            ),
        ]
    )
    raft = await topic(session, "Raft v1", "Raft leader election")
    context = await load_context(session, "topic", raft)
    assert (context.kind, context.topic_id) == ("topic", str(raft))
    assert context.title == "Raft leader election"
    assert context.profile is not None
    assert (context.profile.language, context.profile.depth) == ("de", "intuition-first")
    assert context.profile.current_skills == (SkillLine("Go", "expert"),)
    assert context.profile.target_skills == (SkillLine("Consensus", "proficient"),)
    assert context.profile.background == "SRE"
    assert (await load_context(session, "plan", None)).kind == "plan"


async def revision_of(session: AsyncSession, topic_id: uuid.UUID) -> uuid.UUID:
    revision = await session.scalar(
        select(TopicRevision.id).where(TopicRevision.topic_id == topic_id)
    )
    assert revision is not None
    return revision


async def make_plan(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    election = await topic(session, "Leader election")
    replication = await topic(session, "Log replication")
    plan = StudyPlan(version=3, created_by="run", profile_version=1)
    session.add(plan)
    await session.flush()
    revisions = {t: await revision_of(session, t) for t in (election, replication)}
    session.add_all(
        [
            PlanItem(
                plan_id=plan.id,
                topic_id=election,
                topic_revision_id=revisions[election],
                position=0,
                week_start=1,
                week_span=1,
            ),
            PlanItem(
                plan_id=plan.id,
                topic_id=replication,
                topic_revision_id=revisions[replication],
                position=1,
                week_start=2,
                week_span=1,
                prerequisites=[str(election), "not-a-uuid"],
            ),
            Asset(topic_id=replication, format="figure"),
            Asset(topic_id=replication, format="video", is_current=False),
            StudySession(topic_id=election, minutes=30, rating="confident"),
        ]
    )
    await session.commit()
    return election, replication


async def test_plan_and_topic_tools(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert await plan_summary(session) is None
    election, replication = await make_plan(session)
    tools = build_tutor_tools(session_factory)
    plan = await tools.get_plan_summary()
    assert plan is not None and plan["version"] == 3
    assert [(t["title"], t["status"], t["week_start"]) for t in plan["topics"]] == [
        ("Leader election", "mastered", 1),
        ("Log replication", "not-started", 2),
    ]
    assert plan["topics"][1]["prerequisites"] == ["Leader election"]
    detail = await tools.get_topic(str(replication))
    assert detail is not None
    assert detail["title"] == "Log replication" and detail["in_current_plan"] is True
    assert detail["prerequisites"] == [{"id": str(election), "title": "Leader election"}]
    assert detail["assets"] == ["figure"]
    assert await tools.get_topic(str(uuid.uuid4())) is None
    assert await tools.get_topic("nope") is None


async def test_profile_tool(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tools = build_tutor_tools(session_factory)
    assert await tools.get_profile() is None
    session.add(Profile(display_name="Ada", weekly_budget_hours=4))
    await session.commit()
    profile = await tools.get_profile()
    assert profile is not None and profile["display_name"] == "Ada" and "id" not in profile
    assert profile["preferences"]["depth"] == "intuition-first"


async def test_search_tool_returns_refs_with_source_details(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(type="url", title="Raft (wiki)", uri="https://en.wikipedia.org/wiki/Raft")
    session.add(source)
    await session.commit()
    calls: list[dict[str, Any]] = []
    chunk = uuid.uuid4()

    async def fake(s: AsyncSession, query: str, k: int, **filters: Any) -> list[ChunkHit]:
        calls.append({"query": query, "k": k, **filters})
        return [ChunkHit(chunk, source.id, "Raft (wiki)", {"fragment": "#:~:text=Raft"}, "t", 0.7)]

    monkeypatch.setattr(search, "search_chunks", fake)
    topic_id = uuid.uuid4()
    (ref,) = await build_tutor_tools(session_factory).search_chunks("raft", 4, str(topic_id))
    assert calls == [{"query": "raft", "k": 4, "topic_id": topic_id}]
    assert (ref.chunk_id, ref.source_type, ref.uri) == (str(chunk), "url", source.uri)
    assert ref.label == "[Raft (wiki), “Raft…”]"
