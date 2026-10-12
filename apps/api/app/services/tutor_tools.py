"""Data behind the tutor agent's tools (design D10): retrieval, topic, plan, and profile reads.

Each callable opens its own session (Strands may run tool calls concurrently). Topics and plans
are read straight from the ORM (``topics``, ``topic_revisions``, ``study_plans``,
``plan_items``, ``assets``): sections 6 and 7 ship the topic and plan services in parallel with
this one, so the integrator may route these reads through them.
"""

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import NotFound
from app.db.schema import Asset, PlanItem, Source, StudyPlan, Topic, TopicRevision
from app.services import search
from app.services.profile import get_profile, list_current_skills, list_target_skills, read_profile
from app.services.progress import current_plan, topic_stats
from brainbench_agents.tutor import ChunkRef, SkillLine, TutorContext, TutorProfile, TutorTools

MAX_MERGE_HOPS = 32


class TopicNotFound(NotFound):
    code = "topic-not-found"


async def resolve_topic(session: AsyncSession, topic_id: uuid.UUID) -> Topic:
    """The topic, following ``merged_into`` to the survivor of a merge."""
    seen: set[uuid.UUID] = set()
    current = topic_id
    for _ in range(MAX_MERGE_HOPS):
        topic = await session.get(Topic, current)
        if topic is None or current in seen:
            break
        if topic.merged_into is None:
            return topic
        seen.add(current)
        current = topic.merged_into
    raise TopicNotFound(f"Topic {topic_id} does not exist.")


async def latest_revisions(
    session: AsyncSession, topic_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, TopicRevision]:
    ids = list(dict.fromkeys(topic_ids))
    if not ids:
        return {}
    newest = (
        select(TopicRevision.topic_id, func.max(TopicRevision.revision).label("revision"))
        .where(TopicRevision.topic_id.in_(ids))
        .group_by(TopicRevision.topic_id)
        .subquery()
    )
    rows = await session.scalars(
        select(TopicRevision).join(
            newest,
            (TopicRevision.topic_id == newest.c.topic_id)
            & (TopicRevision.revision == newest.c.revision),
        )
    )
    return {revision.topic_id: revision for revision in rows}


async def current_asset_formats(
    session: AsyncSession, topic_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Formats (``video``, ``figure``) of each topic's current generated assets."""
    ids = list(dict.fromkeys(topic_ids))
    formats: dict[uuid.UUID, list[str]] = {topic_id: [] for topic_id in ids}
    if not ids:
        return formats
    rows = await session.execute(
        select(Asset.topic_id, Asset.format)
        .where(Asset.topic_id.in_(ids), Asset.is_current.is_(True))
        .distinct()
        .order_by(Asset.topic_id, Asset.format.desc())
    )
    for topic_id, fmt in rows:
        formats[topic_id].append(fmt)
    return formats


async def tutor_profile(session: AsyncSession) -> TutorProfile | None:
    profile = await get_profile(session)
    if profile is None:
        return None
    current = await list_current_skills(session, profile.id)
    targets = await list_target_skills(session, profile.id)
    return TutorProfile(
        language=profile.language,
        depth=profile.depth,
        current_skills=tuple(SkillLine(s.name, s.level) for s in current),
        target_skills=tuple(
            SkillLine(t.name, t.target_level) for t in targets if t.achieved_at is None
        ),
        background=profile.background or None,
    )


async def load_context(
    session: AsyncSession, kind: str, topic_id: uuid.UUID | None
) -> TutorContext:
    profile = await tutor_profile(session)
    if kind == "topic" and topic_id is not None:
        revision = (await latest_revisions(session, [topic_id])).get(topic_id)
        return TutorContext(
            "topic",
            topic_id=str(topic_id),
            title=revision.title if revision else None,
            summary=revision.summary if revision else None,
            profile=profile,
        )
    return TutorContext("plan" if kind == "plan" else "general", profile=profile)


async def _sources(session: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Source]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return {}
    return {s.id: s for s in await session.scalars(select(Source).where(Source.id.in_(wanted)))}


async def search_refs(
    session: AsyncSession, query: str, k: int, topic_id: str | None
) -> list[ChunkRef]:
    hits = await search.search_chunks(
        session, query, k, topic_id=uuid.UUID(topic_id) if topic_id else None
    )
    sources = await _sources(session, (hit.source_id for hit in hits))
    refs = []
    for hit in hits:
        source = sources.get(hit.source_id)
        refs.append(
            ChunkRef(
                chunk_id=str(hit.chunk_id),
                source_id=str(hit.source_id),
                source_title=hit.source_title or (source.uri if source else None) or "Untitled",
                location=dict(hit.location or {}),
                text=hit.text,
                score=hit.score,
                source_type=source.type if source else None,
                uri=source.uri if source else None,
            )
        )
    return refs


def _prerequisite_ids(item: PlanItem) -> list[uuid.UUID]:
    ids = []
    for value in item.prerequisites or []:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return ids


async def topic_summary(session: AsyncSession, topic_id: uuid.UUID) -> dict[str, Any]:
    """Title, summary, status, plan placement, prerequisites, and generated assets of a topic."""
    topic = await resolve_topic(session, topic_id)
    revision = (await latest_revisions(session, [topic.id])).get(topic.id)
    stats = (await topic_stats(session, [topic.id]))[topic.id]
    plan = await current_plan(session)
    item = None
    if plan is not None:
        item = await session.scalar(
            select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.topic_id == topic.id)
        )
    prerequisites = _prerequisite_ids(item) if item is not None else []
    titles = await latest_revisions(session, prerequisites)
    return {
        "id": str(topic.id),
        "title": revision.title if revision else None,
        "summary": revision.summary if revision else None,
        "status": stats.status,
        "minutes_studied": stats.minutes,
        "in_current_plan": item is not None and not item.excluded,
        "week_start": item.week_start if item else None,
        "week_span": item.week_span if item else None,
        "estimate_minutes": item.estimate_minutes if item else None,
        "rationale": item.rationale if item else None,
        "prerequisites": [
            {"id": str(p), "title": titles[p].title if p in titles else None} for p in prerequisites
        ],
        "assets": (await current_asset_formats(session, [topic.id]))[topic.id],
    }


async def plan_summary(session: AsyncSession) -> dict[str, Any] | None:
    plan: StudyPlan | None = await current_plan(session)
    if plan is None:
        return None
    items = list(
        await session.scalars(
            select(PlanItem).where(PlanItem.plan_id == plan.id).order_by(PlanItem.position)
        )
    )
    revisions = {
        r.id: r
        for r in await session.scalars(
            select(TopicRevision).where(
                TopicRevision.id.in_([item.topic_revision_id for item in items])
            )
        )
    }
    titles = {item.topic_id: revisions[item.topic_revision_id].title for item in items}
    stats = await topic_stats(session, [item.topic_id for item in items])
    entries = []
    for item in items:
        revision = revisions[item.topic_revision_id]
        entries.append(
            {
                "topic_id": str(item.topic_id),
                "title": revision.title,
                "summary": revision.summary,
                "excluded": item.excluded,
                "week_start": item.week_start,
                "week_span": item.week_span,
                "status": stats[item.topic_id].status,
                "relevance": item.relevance,
                "prerequisites": [titles.get(p, str(p)) for p in _prerequisite_ids(item)],
            }
        )
    return {"version": plan.version, "created_at": plan.created_at, "topics": entries}


async def profile_summary(session: AsyncSession) -> dict[str, Any] | None:
    profile = await get_profile(session)
    if profile is None:
        return None
    read = await read_profile(session, profile)
    return read.model_dump(mode="json", exclude={"id"})


def build_tutor_tools(sessions: async_sessionmaker[AsyncSession]) -> TutorTools:
    """The agent's tool callables, each over its own session."""

    async def search_chunks(query: str, k: int, topic_id: str | None) -> list[ChunkRef]:
        async with sessions() as session:
            return await search_refs(session, query, k, topic_id)

    async def get_topic(topic_id: str) -> dict[str, Any] | None:
        try:
            parsed = uuid.UUID(topic_id)
        except ValueError:
            return None
        async with sessions() as session:
            try:
                return await topic_summary(session, parsed)
            except TopicNotFound:
                return None

    async def get_plan_summary() -> dict[str, Any] | None:
        async with sessions() as session:
            return await plan_summary(session)

    async def get_profile_tool() -> dict[str, Any] | None:
        async with sessions() as session:
            return await profile_summary(session)

    return TutorTools(
        search_chunks=search_chunks,
        get_topic=get_topic,
        get_plan_summary=get_plan_summary,
        get_profile=get_profile_tool,
        k=settings.tutor_retrieval_k,
        min_score=settings.tutor_min_chunk_score,
    )
