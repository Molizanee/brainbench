"""Curation runs and the deterministic steps of the pipeline (design D6).

The ``curation`` job (``app/jobs/curation.py``) orchestrates the agent calls; this module owns
everything that is not an LLM decision: preconditions and run creation, provider routing per
step, chunk windows, embedding dedupe, and reconciliation against existing topics.
"""

import logging
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import DomainError
from app.db.schema import Chunk, CurationRun, Job, Source, TargetSkill, Topic, TopicRevision
from app.services import jobs as job_service

# Module import: app.services.providers imports app.jobs, which imports the curation handler.
from app.services import providers
from app.services.instance_settings import load_instance_settings
from app.services.profile import get_profile
from app.services.topics import (
    add_revision,
    cite_chunks,
    create_topic,
    normalize_title,
    resolve_topic,
)
from brainbench_agents.curation import CURATION_TASK_CLASSES

__lazy_modules__ = {"strands.models.model"}
from strands.models.model import Model

log = logging.getLogger(__name__)

JOB_TYPE = "curation"
DEDUPE_KEY = "curation"
# Route every curation step falls back to when the step itself is not routed explicitly.
PARENT_TASK_CLASS = "curation"


class TargetsRequired(DomainError):
    code = "targets-required"


class ReadySourcesRequired(DomainError):
    code = "ready-sources-required"


def utc_now() -> datetime:
    return datetime.now(UTC)


# --- Preconditions and runs ---------------------------------------------------------------


async def ready_source_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Sources in status "ready", oldest first."""
    rows = await session.scalars(
        select(Source.id).where(Source.status == "ready").order_by(Source.created_at, Source.id)
    )
    return list(rows)


@dataclass(frozen=True)
class CurationInputs:
    profile_version: int
    source_ids: list[uuid.UUID]


async def check_preconditions(session: AsyncSession) -> CurationInputs:
    """At least one target skill and one ready source; raises the matching domain error."""
    profile = await get_profile(session)
    has_target = profile is not None and (
        await session.scalar(
            select(TargetSkill.id).where(TargetSkill.profile_id == profile.id).limit(1)
        )
        is not None
    )
    if profile is None or not has_target:
        raise TargetsRequired(
            "Curation needs at least one target skill. Add one on the profile page (/profile)."
        )
    sources = await ready_source_ids(session)
    if not sources:
        raise ReadySourcesRequired(
            "Curation needs ready material. Add a source and wait until it is ready (/sources)."
        )
    return CurationInputs(profile_version=profile.version, source_ids=sources)


async def effective_task_class(session: AsyncSession, step: str) -> str:
    """The task class whose route a curation step uses.

    A step routed explicitly uses its own route; otherwise an explicit ``curation`` route
    applies to every step; otherwise the step falls back to the default provider.
    """
    routing = (await load_instance_settings(session)).provider_routing or {}
    if step not in routing and PARENT_TASK_CLASS in routing:
        return PARENT_TASK_CLASS
    return step


async def ensure_curation_providers(session: AsyncSession) -> None:
    """``ensure_provider_for`` every step's effective task class (``no-provider``, 400)."""
    for step in CURATION_TASK_CLASSES:
        await providers.ensure_provider_for(session, await effective_task_class(session, step))


@dataclass(frozen=True)
class RoutedModel:
    model: Model
    provider: str
    model_id: str


async def routed_model(session: AsyncSession, step: str) -> RoutedModel:
    """The routed Strands model for a step; usage is recorded under the step's task class."""
    task_class = await effective_task_class(session, step)
    route = await providers.resolve_route(session, task_class)
    model = await providers.model_for(session, task_class)
    if task_class != step:
        model.update_config(task_class=step)
    return RoutedModel(model=model, provider=route.spec.id, model_id=route.model.id)


async def start_run(
    session: AsyncSession, *, threshold: float | None = None
) -> tuple[CurationRun, Job]:
    """Check preconditions and providers, then queue the ``curation`` job with its run row.

    A curation job already queued or running is returned with its run instead of a new one.
    Flushes; the caller commits.
    """
    inputs = await check_preconditions(session)
    await ensure_curation_providers(session)
    run_id = uuid.uuid4()
    job = await job_service.enqueue(
        session, JOB_TYPE, {"run_id": str(run_id)}, dedupe_key=DEDUPE_KEY
    )
    if job.payload.get("run_id") != str(run_id):
        existing = await session.scalar(select(CurationRun).where(CurationRun.job_id == job.id))
        if existing is not None:
            return existing, job
    run = CurationRun(
        id=run_id,
        job_id=job.id,
        profile_version=inputs.profile_version,
        source_ids=[str(s) for s in inputs.source_ids],
        threshold=settings.curation_threshold if threshold is None else threshold,
        models={},
        step_log=[],
    )
    session.add(run)
    await session.flush()
    log.info("curation run %s queued as job %s", run.id, job.id)
    return run, job


# --- Windows ------------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowChunkRef:
    key: str
    chunk_id: uuid.UUID
    text: str
    location: dict[str, Any]


@dataclass(frozen=True)
class SourceWindow:
    source_id: uuid.UUID
    source_title: str
    index: int
    chunks: list[WindowChunkRef]

    @property
    def id(self) -> str:
        return f"{self.source_id}:{self.index}"


def location_label(location: dict[str, Any]) -> str:
    if "page" in location:
        return f"page {location['page']}"
    if "path" in location:
        return str(location["path"])
    return ""


async def source_windows(
    session: AsyncSession, source_id: uuid.UUID, *, window_chars: int
) -> list[SourceWindow]:
    """The source's chunks in order, grouped into windows of about ``window_chars``.

    A window closes before the chunk that would push it past the size; a single chunk larger
    than the size forms its own window. Keys (``C1``...) restart in every window.
    """
    source = await session.get(Source, source_id)
    if source is None:
        return []
    rows = await session.scalars(
        select(Chunk).where(Chunk.source_id == source_id).order_by(Chunk.position)
    )
    windows: list[SourceWindow] = []
    current: list[Chunk] = []
    size = 0
    for chunk in rows:
        if current and size + len(chunk.text) > window_chars:
            windows.append(_window(source, len(windows), current))
            current, size = [], 0
        current.append(chunk)
        size += len(chunk.text)
    if current:
        windows.append(_window(source, len(windows), current))
    return windows


def _window(source: Source, index: int, chunks: list[Chunk]) -> SourceWindow:
    return SourceWindow(
        source_id=source.id,
        source_title=source.title,
        index=index,
        chunks=[
            WindowChunkRef(key=f"C{i}", chunk_id=c.id, text=c.text, location=dict(c.location or {}))
            for i, c in enumerate(chunks, start=1)
        ],
    )


# --- Candidates, dedupe and reconcile -----------------------------------------------------


@dataclass
class Candidate:
    """A topic candidate as checkpointed between steps (chunk ids as strings)."""

    title: str
    summary: str
    chunk_ids: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"title": self.title, "summary": self.summary, "chunk_ids": list(self.chunk_ids)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Candidate:
        return cls(
            title=str(data["title"]),
            summary=str(data["summary"]),
            chunk_ids=[str(c) for c in data.get("chunk_ids", [])],
        )

    @property
    def embedding_text(self) -> str:
        return f"{self.title}\n{self.summary}"


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def cluster_candidates(
    candidates: Sequence[Candidate], vectors: Sequence[Sequence[float]], *, similarity: float
) -> list[list[int]]:
    """Groups of candidate indexes that name the same concept, in first-seen order.

    Two candidates join when their normalized titles are equal or the cosine similarity of
    their embeddings exceeds ``similarity``; groups are the transitive closure.
    """
    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    titles = [normalize_title(c.title) for c in candidates]
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if titles[i] == titles[j] or cosine(vectors[i], vectors[j]) > similarity:
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(candidates)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def union_chunks(members: Sequence[Candidate]) -> list[str]:
    return list(dict.fromkeys(chunk for m in members for chunk in m.chunk_ids))


def fallback_merge(members: Sequence[Candidate]) -> Candidate:
    """Merged candidate when the merge call returned nothing for a group."""
    best = max(members, key=lambda m: len(m.summary))
    return Candidate(title=members[0].title, summary=best.summary, chunk_ids=union_chunks(members))


@dataclass(frozen=True)
class ReconciledTopic:
    topic_id: uuid.UUID
    revision_id: uuid.UUID
    title: str
    summary: str
    created: bool
    revised: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "topic_id": str(self.topic_id),
            "revision_id": str(self.revision_id),
            "title": self.title,
            "summary": self.summary,
            "created": self.created,
            "revised": self.revised,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReconciledTopic:
        return cls(
            topic_id=uuid.UUID(data["topic_id"]),
            revision_id=uuid.UUID(data["revision_id"]),
            title=data["title"],
            summary=data["summary"],
            created=bool(data["created"]),
            revised=bool(data["revised"]),
        )


@dataclass
class _Existing:
    topic_id: uuid.UUID
    title_key: str
    embedding: list[float] | None


async def _existing_topics(session: AsyncSession) -> list[_Existing]:
    """Every topic's latest revision, keyed to the topic it resolves to (merges followed)."""
    rows = await session.execute(
        select(Topic, TopicRevision)
        .join(TopicRevision, TopicRevision.topic_id == Topic.id)
        .order_by(Topic.created_at, Topic.id, TopicRevision.revision.desc())
    )
    seen: set[uuid.UUID] = set()
    out: list[_Existing] = []
    for topic, revision in rows:
        if topic.id in seen:
            continue
        seen.add(topic.id)
        target = topic.id
        if topic.merged_into is not None:
            try:
                target = (await resolve_topic(session, topic.id)).id
            except DomainError:
                continue
        embedding = None if revision.embedding is None else [float(x) for x in revision.embedding]
        out.append(_Existing(target, normalize_title(revision.title), embedding))
    return out


async def reconcile(
    session: AsyncSession,
    run_id: uuid.UUID,
    candidates: Sequence[Candidate],
    vectors: Sequence[list[float]],
    *,
    similarity: float,
) -> list[ReconciledTopic]:
    """Match candidates to existing topics, reuse their ids, and revise only changed content.

    A candidate matches a topic whose current title normalizes to the same text, else the topic
    whose current revision embedding has the highest cosine at or above ``similarity``. A match
    records a new revision only when the summary or the normalized title differs; no match
    creates a topic.
    Chunk citations are unioned into ``topic_chunks``. Two candidates matching one topic are
    folded into the first. Flushes; the caller commits.
    """
    existing = await _existing_topics(session)
    claimed: dict[uuid.UUID, ReconciledTopic] = {}
    out: list[ReconciledTopic] = []
    for candidate, vector in zip(candidates, vectors, strict=True):
        key = normalize_title(candidate.title)
        match = next((e.topic_id for e in existing if e.title_key == key), None)
        if match is None:
            scored = [
                (cosine(vector, e.embedding), e.topic_id)
                for e in existing
                if e.embedding is not None
            ]
            best = max(scored, default=None, key=lambda pair: pair[0])
            if best is not None and best[0] >= similarity:
                match = best[1]
        chunk_ids = {uuid.UUID(c) for c in candidate.chunk_ids}
        if match is not None and match in claimed:
            await cite_chunks(session, match, chunk_ids)
            continue
        if match is None:
            topic, revision = await create_topic(
                session,
                title=candidate.title,
                summary=candidate.summary,
                created_by="run",
                curation_run_id=run_id,
                embedding=vector,
            )
            existing.append(_Existing(topic.id, key, vector))
            result = ReconciledTopic(
                topic.id, revision.id, revision.title, revision.summary, True, False
            )
        else:
            before = await session.scalar(
                select(TopicRevision.id)
                .where(TopicRevision.topic_id == match)
                .order_by(TopicRevision.revision.desc())
                .limit(1)
            )
            current = await session.get(TopicRevision, before)
            # A title that only differs in case, punctuation or spacing is not a change.
            title = (
                current.title
                if current is not None and normalize_title(current.title) == key
                else candidate.title
            )
            revision = await add_revision(
                session,
                match,
                title=title,
                summary=candidate.summary,
                created_by="run",
                curation_run_id=run_id,
                embedding=vector,
            )
            topic_row = await session.get(Topic, match)
            if topic_row is not None:
                topic_row.orphaned_at = None
            result = ReconciledTopic(
                match, revision.id, revision.title, revision.summary, False, revision.id != before
            )
        await cite_chunks(session, result.topic_id, chunk_ids)
        claimed[result.topic_id] = result
        out.append(result)
    await session.flush()
    return out
