"""Shared first step of both visual formats (design D7.1): load what the agents need about a
topic and the learner, and write the storyboard grounded in the topic's chunks.

The job handlers (``app/jobs/video.py``, ``app/jobs/figure.py``) drive this module through
:class:`VisualDeps`, which carries the model factory, the chunk search, and the sandbox runner
so tests can replace each one.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, NotFound
from app.db.schema import (
    Asset,
    Chunk,
    CurrentSkill,
    Profile,
    Source,
    Topic,
    TopicChunk,
    TopicRevision,
)
from app.db.schema import Storyboard as StoryboardRow
from app.jobs.errors import PermanentJobError
from app.services import providers
from app.services import search as chunk_search
from app.services.sandbox import SandboxRunner

__lazy_modules__ = {
    "brainbench_agents.visual",
    "brainbench_providers.strands_models",
    "strands.models.model",
}
from strands.models.model import Model

from brainbench_agents.visual import (
    ChunkRef,
    Depth,
    Format,
    Learner,
    SearchFn,
    Storyboard,
    StoryboardRequest,
    generate_storyboard,
)
from brainbench_providers.strands_models import ProviderRetryStrategy

log = logging.getLogger(__name__)

# Agent step -> provider task class (the routing UI lists the task classes the providers
# package registers; see notes/wave3-visual.md for the brief's names).
STEP_TASK_CLASSES: dict[str, str] = {
    "storyboard": "storyboarding",
    "figure_brief": "storyboarding",
    "manim_script": "script-generation",
    "script_repair": "script-repair",
    "figure": "figure-building",
}
MAX_MERGE_DEPTH = 32
SEED_CHUNKS = 8

type ModelFactory = Callable[[AsyncSession, str], Awaitable[Model]]
type SearchFactory = Callable[[AsyncSession, uuid.UUID], SearchFn]


class TopicNotFound(NotFound):
    code = "topic-not-found"


class TopicNotGrounded(DomainError):
    code = "topic-without-chunks"


async def step_model(session: AsyncSession, step: str) -> Model:
    """The routed model for an agent step (records provider usage under its task class)."""
    return await providers.model_for(session, STEP_TASK_CLASSES[step])


def topic_search(session: AsyncSession, topic_id: uuid.UUID) -> SearchFn:
    """``search_chunks`` scoped to the chunks the topic references."""

    async def search(query: str, k: int) -> Sequence[ChunkRef]:
        hits = await chunk_search.search_chunks(session, query, k, topic_id=topic_id)
        return [
            ChunkRef(str(h.chunk_id), h.source_title, h.text, dict(h.location), h.score)
            for h in hits
        ]

    return search


@dataclass
class VisualDeps:
    """What the visual job handlers call out to; tests pass fakes."""

    model: ModelFactory = step_model
    search: SearchFactory = topic_search
    sandbox: SandboxRunner = field(default_factory=SandboxRunner)
    retry_strategy: Any = field(default_factory=ProviderRetryStrategy)


async def agent_step[T](call: Coroutine[Any, Any, T]) -> T:
    """Await an agent call; a provider throttle becomes a deferral of the job."""
    try:
        return await call
    except Exception as exc:
        if providers.find_throttle(exc) is not None:
            raise providers.provider_limit_from(exc) from exc
        raise


async def routed_model(deps: VisualDeps, session: AsyncSession, step: str) -> Model:
    """The model for ``step``; a missing or locked provider fails the job without retries."""
    try:
        return await deps.model(session, step)
    except DomainError as exc:
        raise PermanentJobError(exc.message, reason=exc.code) from exc


# --- topic and learner ------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicContext:
    id: uuid.UUID
    title: str
    summary: str
    revision: int
    chunk_ids: list[str]


async def resolve_topic(session: AsyncSession, topic_id: uuid.UUID) -> Topic:
    """The topic, or the survivor of its merge chain (at most 32 hops)."""
    topic = await session.get(Topic, topic_id)
    for _ in range(MAX_MERGE_DEPTH):
        if topic is None or topic.merged_into is None:
            break
        topic = await session.get(Topic, topic.merged_into)
    if topic is None or topic.merged_into is not None:
        raise TopicNotFound(f"Topic {topic_id} does not exist.")
    return topic


async def latest_revision(session: AsyncSession, topic_id: uuid.UUID) -> TopicRevision | None:
    stmt = (
        select(TopicRevision)
        .where(TopicRevision.topic_id == topic_id)
        .order_by(TopicRevision.revision.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def topic_chunk_ids(session: AsyncSession, topic_id: uuid.UUID) -> list[str]:
    stmt = (
        select(Chunk.id)
        .join(TopicChunk, TopicChunk.chunk_id == Chunk.id)
        .where(TopicChunk.topic_id == topic_id)
        .order_by(Chunk.source_id, Chunk.position)
    )
    return [str(i) for i in (await session.execute(stmt)).scalars()]


async def load_topic(session: AsyncSession, topic_id: uuid.UUID) -> TopicContext:
    topic = await resolve_topic(session, topic_id)
    revision = await latest_revision(session, topic.id)
    if revision is None:
        raise TopicNotFound(f"Topic {topic_id} has no revision.")
    chunk_ids = await topic_chunk_ids(session, topic.id)
    if not chunk_ids:
        raise TopicNotGrounded(
            f"Topic {revision.title!r} references no source chunks, so a storyboard cannot be "
            "grounded; re-run curation after adding sources."
        )
    return TopicContext(topic.id, revision.title, revision.summary, revision.revision, chunk_ids)


async def chunks_by_id(session: AsyncSession, ids: Sequence[str]) -> list[ChunkRef]:
    """Chunks in the order of ``ids`` (unknown ids are skipped)."""
    wanted = [uuid.UUID(i) for i in dict.fromkeys(ids)]
    if not wanted:
        return []
    stmt = (
        select(Chunk.id, Chunk.text, Chunk.location, Source.title)
        .join(Source, Source.id == Chunk.source_id)
        .where(Chunk.id.in_(wanted))
    )
    rows = {row.id: row for row in await session.execute(stmt)}
    return [
        ChunkRef(str(i), rows[i].title, rows[i].text, dict(rows[i].location or {}))
        for i in wanted
        if i in rows
    ]


async def load_learner(session: AsyncSession) -> Learner:
    """Language, depth, background and skills from the profile (defaults without one)."""
    profile = (await session.execute(select(Profile).limit(1))).scalar_one_or_none()
    if profile is None:
        return Learner()
    skills = (
        await session.execute(select(CurrentSkill).where(CurrentSkill.profile_id == profile.id))
    ).scalars()
    depth: Depth = "rigor-first" if profile.depth == "rigor-first" else "intuition-first"
    return Learner(
        language=profile.language,
        depth=depth,
        background=profile.background,
        skills=[f"{s.name} ({s.level})" for s in skills],
    )


# --- previous asset (regeneration) ------------------------------------------------------------


@dataclass(frozen=True)
class PreviousVersion:
    asset: Asset
    storyboard: Storyboard | None
    figure_brief: dict[str, Any] | None


def storyboard_from_row(row: StoryboardRow, title: str) -> Storyboard | None:
    try:
        return Storyboard.model_validate({"title": title or "Storyboard", "scenes": row.scenes})
    except ValueError:
        log.warning("storyboard %s does not validate; ignored as previous version", row.id)
        return None


async def load_previous(session: AsyncSession, asset_id: str | None) -> PreviousVersion | None:
    if not asset_id:
        return None
    asset = await session.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        return None
    row = await session.get(StoryboardRow, asset.storyboard_id) if asset.storyboard_id else None
    title = str(asset.provenance.get("storyboard_title", ""))
    return PreviousVersion(
        asset=asset,
        storyboard=storyboard_from_row(row, title) if row is not None else None,
        figure_brief=row.figure_brief if row is not None else None,
    )


# --- the storyboard step ----------------------------------------------------------------------


async def write_storyboard(
    deps: VisualDeps,
    session: AsyncSession,
    topic: TopicContext,
    format: Format,
    *,
    previous: PreviousVersion | None = None,
    feedback: str | None = None,
) -> Storyboard:
    learner = await load_learner(session)
    request = StoryboardRequest(
        topic_title=topic.title,
        topic_summary=topic.summary,
        format=format,
        learner=learner,
        seed_chunks=await chunks_by_id(session, topic.chunk_ids[:SEED_CHUNKS]),
        previous=previous.storyboard if previous else None,
        feedback=feedback,
    )
    model = await routed_model(deps, session, "storyboard")
    return await agent_step(
        generate_storyboard(
            model,
            deps.search(session, topic.id),
            request,
            allowed_chunk_ids=topic.chunk_ids,
            retry_strategy=deps.retry_strategy,
        )
    )


def parse_payload(payload: dict[str, Any]) -> tuple[uuid.UUID, str | None, str | None]:
    """``(topic_id, feedback, previous_asset_id)`` from a generation job payload."""
    try:
        topic_id = uuid.UUID(str(payload["topic_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"invalid generation payload: {payload!r}") from exc
    feedback = payload.get("feedback") or None
    previous = payload.get("previous_asset_id") or None
    return topic_id, feedback, previous


async def topic_for_job(session: AsyncSession, topic_id: uuid.UUID) -> TopicContext:
    try:
        return await load_topic(session, topic_id)
    except DomainError as exc:
        raise PermanentJobError(exc.message, reason=exc.code) from exc
