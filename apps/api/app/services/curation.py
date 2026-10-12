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
from app.db.schema import (
    Chunk,
    CurationRun,
    Job,
    Source,
    StudyPlan,
    StudySession,
    TargetSkill,
    Topic,
    TopicRevision,
)
from app.models.curation import CurationRunCreate, CurationRunRead, ModelUsed
from app.services import jobs as job_service
from app.services import providers
from app.services.instance_settings import load_instance_settings
from app.services.profile import (
    get_profile,
    list_current_skills,
    list_target_skills,
    normalize_name,
)
from app.services.topics import (
    add_revision,
    cite_chunks,
    create_topic,
    current_revision,
    normalize_title,
    resolve_topic,
)
from brainbench_agents.curation import CURATION_TASK_CLASSES, TopicScore

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
    """One revision of an existing topic, keyed to the topic it resolves to."""

    topic_id: uuid.UUID
    title_key: str
    embedding: list[float] | None
    current: bool


async def _existing_topics(session: AsyncSession) -> list[_Existing]:
    """Every revision of every topic, current revisions first (merges followed)."""
    rows = await session.execute(
        select(Topic, TopicRevision)
        .join(TopicRevision, TopicRevision.topic_id == Topic.id)
        .order_by(Topic.created_at, Topic.id, TopicRevision.revision.desc())
    )
    resolved: dict[uuid.UUID, uuid.UUID | None] = {}
    out: list[_Existing] = []
    for topic, revision in rows:
        current = topic.id not in resolved
        if current:
            target: uuid.UUID | None = topic.id
            if topic.merged_into is not None:
                try:
                    target = (await resolve_topic(session, topic.id)).id
                except DomainError:
                    target = None
            resolved[topic.id] = target
        target_id = resolved[topic.id]
        if target_id is None:
            continue
        embedding = None if revision.embedding is None else [float(x) for x in revision.embedding]
        out.append(_Existing(target_id, normalize_title(revision.title), embedding, current))
    out.sort(key=lambda e: not e.current)
    return out


def _match(
    existing: Sequence[_Existing], key: str, vector: Sequence[float], similarity: float
) -> uuid.UUID | None:
    by_title = next((e.topic_id for e in existing if e.title_key == key), None)
    if by_title is not None:
        return by_title
    scored = [(cosine(vector, e.embedding), e.topic_id) for e in existing if e.embedding]
    best = max(scored, default=None, key=lambda pair: pair[0])
    return best[1] if best is not None and best[0] >= similarity else None


async def reconcile(
    session: AsyncSession,
    run_id: uuid.UUID,
    candidates: Sequence[Candidate],
    vectors: Sequence[list[float]],
    *,
    similarity: float,
) -> list[ReconciledTopic]:
    """Match candidates to existing topics, reuse their ids, and revise only changed content.

    A candidate matches the topic with a revision whose title normalizes to the same text
    (current revisions first, so a topic the engineer renamed is still found by its old
    title), else the topic with the revision embedding of highest cosine at or above
    ``similarity``. A match records a new revision only when the summary or the normalized
    title differs, and never over a revision the engineer wrote (``created_by="edit"``); no match
    creates a topic. Chunk citations are unioned into ``topic_chunks``. Two candidates matching
    one topic are folded into the first. Flushes; the caller commits.
    """
    existing = await _existing_topics(session)
    claimed: dict[uuid.UUID, ReconciledTopic] = {}
    out: list[ReconciledTopic] = []
    for candidate, vector in zip(candidates, vectors, strict=True):
        key = normalize_title(candidate.title)
        match = _match(existing, key, vector, similarity)
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
            existing.append(_Existing(topic.id, key, vector, True))
            result = ReconciledTopic(
                topic.id, revision.id, revision.title, revision.summary, True, False
            )
        else:
            current = await current_revision(session, match)
            revision = current
            if current.created_by != "edit":
                # A title that only differs in case, punctuation or spacing is not a change.
                same_title = normalize_title(current.title) == key
                revision = await add_revision(
                    session,
                    match,
                    title=current.title if same_title else candidate.title,
                    summary=candidate.summary,
                    created_by="run",
                    curation_run_id=run_id,
                    embedding=vector,
                )
            topic_row = await session.get(Topic, match)
            if topic_row is not None:
                topic_row.orphaned_at = None
            result = ReconciledTopic(
                match,
                revision.id,
                revision.title,
                revision.summary,
                False,
                revision.id != current.id,
            )
        await cite_chunks(session, result.topic_id, chunk_ids)
        claimed[result.topic_id] = result
        out.append(result)
    await session.flush()
    return out


# --- Scoring ------------------------------------------------------------------------------

HELD_RELEVANCE_CAP = 0.15
UNRELATED_PREFIX = "Unrelated to your target skills."


async def profile_snapshot(session: AsyncSession) -> dict[str, Any]:
    """The profile as the scoring prompt and the ``get_profile`` tool present it."""
    profile = await get_profile(session)
    if profile is None:
        return {}
    skills = await list_current_skills(session, profile.id)
    targets = await list_target_skills(session, profile.id)
    return {
        "background": profile.background,
        "weekly_budget_hours": profile.weekly_budget_hours,
        "preferences": {
            "formats": list(profile.preferred_formats),
            "depth": profile.depth,
            "language": profile.language,
        },
        "current_skills": [{"name": s.name, "level": s.level} for s in skills],
        "target_skills": [
            {
                "name": t.name,
                "target_level": t.target_level,
                "priority": t.priority,
                "reason": t.reason,
                "achieved": t.achieved_at is not None,
            }
            for t in targets
        ],
    }


def session_summaries(sessions: Sequence[StudySession]) -> list[dict[str, Any]]:
    return [
        {
            "studied_on": s.studied_at.date().isoformat(),
            "minutes": s.minutes,
            "rating": s.rating,
        }
        for s in sessions
    ]


@dataclass(frozen=True)
class ScoredTopic:
    topic_id: uuid.UUID
    relevance: float
    serves_targets: list[str]
    rationale: str
    already_held: bool
    estimate_minutes: int

    def to_json(self) -> dict[str, Any]:
        return {
            "topic_id": str(self.topic_id),
            "relevance": self.relevance,
            "serves_targets": list(self.serves_targets),
            "rationale": self.rationale,
            "already_held": self.already_held,
            "estimate_minutes": self.estimate_minutes,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ScoredTopic:
        return cls(
            topic_id=uuid.UUID(data["topic_id"]),
            relevance=float(data["relevance"]),
            serves_targets=[str(t) for t in data["serves_targets"]],
            rationale=str(data["rationale"]),
            already_held=bool(data["already_held"]),
            estimate_minutes=int(data["estimate_minutes"]),
        )


def apply_score(
    topic_id: uuid.UUID,
    score: TopicScore | None,
    targets: Sequence[TargetSkill],
    *,
    default_estimate: int,
) -> ScoredTopic:
    """Validate one scoring answer into the stored form.

    Target names map to target ids (unknown names are dropped); a topic serving no target says
    it is unrelated; already-held material is capped below 0.2 and says so; a topic the model
    did not score gets relevance 0 with that reason.
    """
    if score is None:
        return ScoredTopic(
            topic_id,
            0.0,
            [],
            "The scoring step returned no score for this topic.",
            False,
            default_estimate,
        )
    by_name = {normalize_name(t.name): str(t.id) for t in targets}
    by_id = {str(t.id) for t in targets}
    serves: list[str] = []
    for name in score.serves_targets:
        target_id = name if name in by_id else by_name.get(normalize_name(name))
        if target_id is not None and target_id not in serves:
            serves.append(target_id)
    relevance = min(max(score.relevance, 0.0), 1.0)
    rationale = " ".join(score.rationale.split())
    if score.already_held:
        relevance = min(relevance, HELD_RELEVANCE_CAP)
        if "already" not in rationale.casefold():
            rationale = f"You already hold this skill. {rationale}".strip()
    if not serves and "unrelated" not in rationale.casefold():
        rationale = f"{UNRELATED_PREFIX} {rationale}".strip()
    return ScoredTopic(
        topic_id=topic_id,
        relevance=round(relevance, 4),
        serves_targets=serves,
        rationale=rationale,
        already_held=score.already_held,
        estimate_minutes=score.estimate_minutes,
    )


# --- HTTP-facing --------------------------------------------------------------------------


async def read_run(session: AsyncSession, run: CurationRun) -> CurationRunRead:
    job = await session.get(Job, run.job_id) if run.job_id else None
    plan_version = await session.scalar(
        select(StudyPlan.version).where(
            StudyPlan.curation_run_id == run.id, StudyPlan.created_by == "run"
        )
    )
    await session.refresh(run, ["created_at"])
    return CurationRunRead(
        id=run.id,
        job_id=run.job_id,
        status=job.status if job else None,
        progress=job.progress if job else None,
        error=job.error if job else None,
        profile_version=run.profile_version,
        source_ids=[uuid.UUID(s) for s in run.source_ids],
        threshold=run.threshold,
        models={k: ModelUsed.model_validate(v) for k, v in (run.models or {}).items()},
        step_log=list(run.step_log or []),
        plan_version=plan_version,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


class CurationService:
    """Start and list curation runs (commits)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(self, body: CurationRunCreate) -> CurationRunRead:
        run, _ = await start_run(self.session, threshold=body.threshold)
        await self.session.commit()
        return await read_run(self.session, run)

    async def list_runs(self, *, limit: int = 20) -> list[CurationRunRead]:
        rows = await self.session.scalars(
            select(CurationRun).order_by(CurationRun.created_at.desc(), CurationRun.id).limit(limit)
        )
        return [await read_run(self.session, run) for run in list(rows)]
