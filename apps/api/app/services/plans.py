"""Study plan versions (design D6): immutable ``study_plans`` rows with their ``plan_items``.

A curation run writes a new version from its scored topics; every edit copies the current
version forward into a new one. Items reference ``(topic_id, topic_revision_id)`` so an old
version keeps rendering the text it was built with. Functions flush; the caller commits.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, DomainError, NotFound
from app.db.schema import (
    Chunk,
    CurationRun,
    PlanItem,
    Source,
    StudyPlan,
    TargetSkill,
    TopicChunk,
    TopicRevision,
)
from app.models.curation import ModelUsed
from app.models.plans import (
    Citation,
    IncludeEdit,
    MergeEdit,
    PlanEdit,
    PlanItemRead,
    PlanRead,
    PlanRunInfo,
    PlanWeek,
    RemoveEdit,
    ReorderEdit,
    TargetRef,
    TopicEdit,
    TopicRef,
)

# Module import: curation imports providers, which imports app.jobs and this module.
from app.services import curation as curation_service
from app.services import embeddings
from app.services.planner import (
    NO_PRIORITY,
    PRIORITY_RANK,
    Edge,
    PlanTopic,
    Violation,
    check_order,
    insert_position,
    pack_weeks,
    plan_topics,
    sort_key,
    stable_order,
)
from app.services.profile import get_profile, list_target_skills, normalize_name
from app.services.progress import mastered_topic_ids, promote_achieved_targets, topic_stats
from app.services.topics import (
    TopicNotFound,
    add_revision,
    current_revision,
    merge_topics,
    resolve_topic,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ItemDraft:
    """One plan item as written; ``prerequisites`` are topic ids (strings)."""

    topic_id: uuid.UUID
    topic_revision_id: uuid.UUID
    title: str
    relevance: float | None
    rationale: str | None
    serves_targets: list[str]
    estimate_minutes: int | None
    prerequisites: list[str] = field(default_factory=list)
    excluded: bool = False
    manually_included: bool = False
    week_start: int | None = None
    week_span: int | None = None
    exceeds_weekly_budget: bool = False


def priority_rank(serves: Sequence[str], targets: Sequence[TargetSkill]) -> int:
    """Best priority among the active targets a topic serves (no target ranks last)."""
    ranks = [
        PRIORITY_RANK.get(t.priority, NO_PRIORITY)
        for t in targets
        if str(t.id) in serves and t.achieved_at is None
    ]
    return min(ranks, default=NO_PRIORITY)


async def budget_minutes(session: AsyncSession) -> int:
    profile = await get_profile(session)
    return max(1, round((profile.weekly_budget_hours if profile else 1.0) * 60))


async def schedule(
    session: AsyncSession, included: Sequence[ItemDraft], edges: Sequence[Edge]
) -> list[ItemDraft]:
    """Order and pack the included items with the planner; prerequisites set from ``edges``."""
    profile = await get_profile(session)
    targets = await list_target_skills(session, profile.id) if profile else []
    mastered = await mastered_topic_ids(session)
    by_key = {str(d.topic_id): d for d in included}
    topics = [
        PlanTopic(
            key=key,
            estimate_minutes=d.estimate_minutes or 0,
            relevance=d.relevance or 0.0,
            priority_rank=priority_rank(d.serves_targets, targets),
            mastered=d.topic_id in mastered,
            title=d.title,
        )
        for key, d in by_key.items()
    ]
    placements = plan_topics(topics, edges, await budget_minutes(session))
    return [
        pack(by_key[p.key], p.week_start, p.week_span, p.exceeds_weekly_budget, edges)
        for p in placements
    ]


def pack(
    draft: ItemDraft, week_start: int, week_span: int, exceeds: bool, edges: Sequence[Edge]
) -> ItemDraft:
    key = str(draft.topic_id)
    return replace(
        draft,
        week_start=week_start,
        week_span=week_span,
        exceeds_weekly_budget=exceeds,
        prerequisites=sorted({e.requires for e in edges if e.topic == key}),
    )


async def next_version(session: AsyncSession) -> int:
    latest = await session.scalar(select(func.max(StudyPlan.version)))
    return (latest or 0) + 1


async def write_plan(
    session: AsyncSession,
    *,
    created_by: str,
    profile_version: int,
    source_ids: Sequence[str],
    included: Sequence[ItemDraft],
    excluded: Sequence[ItemDraft],
    curation_run_id: uuid.UUID | None = None,
) -> StudyPlan:
    """Insert the next plan version: included items in order, then the excluded ones.

    Promotes targets whose serving topics are all mastered in the new plan (learning-progress).
    """
    plan = StudyPlan(
        version=await next_version(session),
        curation_run_id=curation_run_id,
        created_by=created_by,
        profile_version=profile_version,
        source_ids=list(source_ids),
    )
    session.add(plan)
    await session.flush()
    rows = [
        *included,
        *(
            replace(d, excluded=True, week_start=None, week_span=None, exceeds_weekly_budget=False)
            for d in excluded
        ),
    ]
    session.add_all(
        PlanItem(
            plan_id=plan.id,
            topic_id=d.topic_id,
            topic_revision_id=d.topic_revision_id,
            position=position,
            week_start=d.week_start,
            week_span=d.week_span,
            exceeds_weekly_budget=d.exceeds_weekly_budget,
            prerequisites=list(d.prerequisites),
            manually_included=d.manually_included,
            excluded=d.excluded,
            relevance=d.relevance,
            rationale=d.rationale,
            serves_targets=list(d.serves_targets),
            estimate_minutes=d.estimate_minutes,
        )
        for position, d in enumerate(rows)
    )
    await session.flush()
    await promote_achieved_targets(session)
    log.info("plan version %d written (%s, %d items)", plan.version, created_by, len(rows))
    return plan


@dataclass(frozen=True)
class RunTopic:
    """A run's topic with its score, as the planning step receives it."""

    topic_id: uuid.UUID
    revision_id: uuid.UUID
    title: str
    relevance: float
    serves_targets: list[str]
    rationale: str
    estimate_minutes: int


async def _current(session: AsyncSession, topic: RunTopic) -> RunTopic:
    """Follow a merge made while the run was deferred: plan the survivor's current text."""
    resolved = await resolve_topic(session, topic.topic_id)
    if resolved.id == topic.topic_id:
        return topic
    revision = await current_revision(session, resolved.id)
    return replace(topic, topic_id=resolved.id, revision_id=revision.id, title=revision.title)


async def write_run_plan(
    session: AsyncSession,
    run: CurationRun,
    topics: Sequence[RunTopic],
    edges: Sequence[Edge],
) -> StudyPlan:
    """The plan a curation run produces: topics at or above the run threshold are ordered and
    packed into weeks; the others are listed as excluded, most relevant first."""
    seen: set[uuid.UUID] = set()
    current: list[RunTopic] = []
    alias: dict[str, str] = {}
    for topic in topics:
        resolved = await _current(session, topic)
        alias[str(topic.topic_id)] = str(resolved.topic_id)
        if resolved.topic_id not in seen:
            seen.add(resolved.topic_id)
            current.append(resolved)
    edges = [
        replace(e, topic=alias.get(e.topic, e.topic), requires=alias.get(e.requires, e.requires))
        for e in edges
    ]
    drafts = [
        ItemDraft(
            topic_id=t.topic_id,
            topic_revision_id=t.revision_id,
            title=t.title,
            relevance=t.relevance,
            rationale=t.rationale,
            serves_targets=list(t.serves_targets),
            estimate_minutes=t.estimate_minutes,
        )
        for t in current
    ]
    kept = [d for d in drafts if (d.relevance or 0.0) >= run.threshold]
    dropped = sorted(
        (d for d in drafts if (d.relevance or 0.0) < run.threshold),
        key=lambda d: (-(d.relevance or 0.0), d.title.casefold()),
    )
    return await write_plan(
        session,
        created_by="run",
        profile_version=run.profile_version,
        source_ids=run.source_ids,
        included=await schedule(session, kept, edges),
        excluded=dropped,
        curation_run_id=run.id,
    )


# --- Reading ------------------------------------------------------------------------------

MAX_CITATIONS = 8
EXCERPT_CHARS = 280
REMOVED_NOTE = "Removed from the plan by you."


class PlanNotFound(NotFound):
    code = "plan-not-found"


class PlanMissing(NotFound):
    code = "plan-missing"


class PlanOutdated(Conflict):
    code = "plan-outdated"


class TopicNotInPlan(NotFound):
    code = "topic-not-in-plan"


class InvalidEdit(DomainError):
    code = "invalid-edit"


class PrerequisiteViolation(DomainError):
    code = "prerequisite-violation"


async def latest_version(session: AsyncSession) -> int | None:
    return await session.scalar(select(func.max(StudyPlan.version)))


async def get_plan(session: AsyncSession, version: int) -> StudyPlan:
    plan = await session.scalar(select(StudyPlan).where(StudyPlan.version == version))
    if plan is None:
        raise PlanNotFound(f"Plan version {version} does not exist.")
    return plan


async def require_current_plan(session: AsyncSession) -> StudyPlan:
    version = await latest_version(session)
    if version is None:
        raise PlanMissing("No study plan yet. Run curation to build one.")
    return await get_plan(session, version)


async def load_items(
    session: AsyncSession, plan: StudyPlan
) -> list[tuple[PlanItem, TopicRevision]]:
    rows = await session.execute(
        select(PlanItem, TopicRevision)
        .join(TopicRevision, TopicRevision.id == PlanItem.topic_revision_id)
        .where(PlanItem.plan_id == plan.id)
        .order_by(PlanItem.position)
    )
    return [(item, revision) for item, revision in rows]


async def stale_reasons(session: AsyncSession, plan: StudyPlan) -> list[str]:
    """Why the plan no longer matches its inputs: a newer profile or a different ready set."""
    reasons = []
    profile = await get_profile(session)
    if profile is not None and profile.version != plan.profile_version:
        reasons.append(
            f"Your profile changed (version {profile.version}; this plan used "
            f"version {plan.profile_version})."
        )
    ready = {str(s) for s in await curation_service.ready_source_ids(session)}
    if ready != {str(s) for s in plan.source_ids}:
        added, removed = len(ready - set(plan.source_ids)), len(set(plan.source_ids) - ready)
        parts = [f"{added} added" if added else "", f"{removed} removed" if removed else ""]
        reasons.append(f"Your ready sources changed ({', '.join(p for p in parts if p)}).")
    return reasons


def citation_url(source: Source, location: dict[str, Any]) -> str | None:
    if source.type == "url" and source.uri:
        base = source.uri.split("#", 1)[0]
        return base + str(location.get("fragment", ""))
    if source.type == "git" and source.uri:
        return source.uri
    return None


async def citations_for(
    session: AsyncSession, topic_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[Citation]]:
    out: dict[uuid.UUID, list[Citation]] = {t: [] for t in topic_ids}
    if not topic_ids:
        return out
    rows = await session.execute(
        select(TopicChunk.topic_id, Chunk, Source)
        .join(Chunk, Chunk.id == TopicChunk.chunk_id)
        .join(Source, Source.id == Chunk.source_id)
        .where(TopicChunk.topic_id.in_(list(topic_ids)))
        .order_by(Source.created_at, Source.id, Chunk.position)
    )
    for topic_id, chunk, source in rows:
        if len(out[topic_id]) >= MAX_CITATIONS:
            continue
        text = " ".join(chunk.text.split())
        out[topic_id].append(
            Citation(
                chunk_id=chunk.id,
                source_id=source.id,
                source_title=source.title,
                source_type=source.type,
                location=dict(chunk.location or {}),
                url=citation_url(source, chunk.location or {}),
                excerpt=text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else ""),
            )
        )
    return out


def _target_refs(serves: Sequence[str], targets: Sequence[TargetSkill]) -> list[TargetRef]:
    refs: list[TargetRef] = []
    for entry in serves:
        found = next(
            (
                t
                for t in targets
                if str(t.id) == entry or t.name_normalized == normalize_name(entry)
            ),
            None,
        )
        if found is not None and all(r.id != found.id for r in refs):
            refs.append(TargetRef(id=found.id, name=found.name))
    return refs


async def read_plan(session: AsyncSession, plan: StudyPlan) -> PlanRead:
    rows = await load_items(session, plan)
    survivors: dict[uuid.UUID, uuid.UUID] = {}
    for item, _ in rows:
        try:
            survivors[item.topic_id] = (await resolve_topic(session, item.topic_id)).id
        except TopicNotFound:
            survivors[item.topic_id] = item.topic_id
    live_ids = list(dict.fromkeys(survivors.values()))
    stats = await topic_stats(session, live_ids)
    citations = await citations_for(session, live_ids)
    profile = await get_profile(session)
    targets = await list_target_skills(session, profile.id) if profile else []
    titles = {item.topic_id: revision.title for item, revision in rows}

    def to_read(item: PlanItem, revision: TopicRevision) -> PlanItemRead:
        live = survivors[item.topic_id]
        return PlanItemRead(
            topic_id=item.topic_id,
            topic_revision_id=item.topic_revision_id,
            revision=revision.revision,
            title=revision.title,
            summary=revision.summary,
            position=item.position,
            week_start=item.week_start,
            week_span=item.week_span,
            exceeds_weekly_budget=item.exceeds_weekly_budget,
            prerequisites=[
                TopicRef(topic_id=uuid.UUID(p), title=titles[uuid.UUID(p)])
                for p in item.prerequisites
                if uuid.UUID(p) in titles
            ],
            manually_included=item.manually_included,
            excluded=item.excluded,
            relevance=item.relevance,
            rationale=item.rationale,
            serves_targets=_target_refs(item.serves_targets, targets),
            estimate_minutes=item.estimate_minutes,
            status=stats[live].status,
            merged_into=None if live == item.topic_id else live,
            citations=citations[live],
        )

    items = [to_read(i, r) for i, r in rows if not i.excluded]
    weeks: dict[int, PlanWeek] = {}
    for read in items:
        if read.week_start is None:
            continue
        week = weeks.setdefault(
            read.week_start,
            PlanWeek(
                week_start=read.week_start,
                week_span=1,
                exceeds_weekly_budget=False,
                total_minutes=0,
                topic_ids=[],
            ),
        )
        week.week_span = max(week.week_span, read.week_span or 1)
        week.exceeds_weekly_budget = week.exceeds_weekly_budget or read.exceeds_weekly_budget
        week.total_minutes += read.estimate_minutes or 0
        week.topic_ids.append(read.topic_id)
    run = await session.get(CurationRun, plan.curation_run_id) if plan.curation_run_id else None
    current = await latest_version(session) or plan.version
    reasons = await stale_reasons(session, plan)
    await session.refresh(plan, ["created_at"])
    return PlanRead(
        version=plan.version,
        created_at=plan.created_at,
        created_by=plan.created_by,
        curation_run_id=plan.curation_run_id,
        profile_version=plan.profile_version,
        source_ids=[uuid.UUID(s) for s in plan.source_ids],
        current_version=current,
        is_current=current == plan.version,
        stale=bool(reasons),
        stale_reasons=reasons,
        weekly_budget_minutes=await budget_minutes(session) if profile else None,
        run=None
        if run is None
        else PlanRunInfo(
            id=run.id,
            threshold=run.threshold,
            models={k: ModelUsed.model_validate(v) for k, v in (run.models or {}).items()},
            finished_at=run.finished_at,
        ),
        weeks=sorted(weeks.values(), key=lambda w: w.week_start),
        items=items,
        excluded=[to_read(i, r) for i, r in rows if i.excluded],
    )


# --- Editing ------------------------------------------------------------------------------


def _draft(item: PlanItem, revision: TopicRevision) -> ItemDraft:
    return ItemDraft(
        topic_id=item.topic_id,
        topic_revision_id=revision.id,
        title=revision.title,
        relevance=item.relevance,
        rationale=item.rationale,
        serves_targets=[str(s) for s in item.serves_targets],
        estimate_minutes=item.estimate_minutes,
        prerequisites=[str(p) for p in item.prerequisites],
        excluded=item.excluded,
        manually_included=item.manually_included,
        week_start=item.week_start,
        week_span=item.week_span,
        exceeds_weekly_budget=item.exceeds_weekly_budget,
    )


def _edges(included: Sequence[ItemDraft]) -> list[Edge]:
    present = {str(d.topic_id) for d in included}
    return [
        Edge(topic=str(d.topic_id), requires=p)
        for d in included
        for p in d.prerequisites
        if p in present
    ]


async def repack(
    session: AsyncSession, ordered: Sequence[ItemDraft], edges: Sequence[Edge]
) -> list[ItemDraft]:
    """Weekly blocks for an edited order (the order itself is kept)."""
    placements = pack_weeks(
        [str(d.topic_id) for d in ordered],
        {str(d.topic_id): d.estimate_minutes or 0 for d in ordered},
        await budget_minutes(session),
    )
    by_key = {str(d.topic_id): d for d in ordered}
    return [
        pack(by_key[p.key], p.week_start, p.week_span, p.exceeds_weekly_budget, edges)
        for p in placements
    ]


def _violation_message(violations: Sequence[Violation], titles: dict[str, str]) -> str:
    parts = [
        f"'{titles[v.topic]}' must come after its prerequisite '{titles[v.requires]}'."
        for v in violations
    ]
    return " ".join(parts)


class PlanService:
    """HTTP-facing plan operations; every edit writes a new version and commits."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def versions(self) -> list[StudyPlan]:
        rows = await self.session.scalars(select(StudyPlan).order_by(StudyPlan.version.desc()))
        return list(rows)

    async def current(self) -> PlanRead:
        return await read_plan(self.session, await require_current_plan(self.session))

    async def get(self, version: int) -> PlanRead:
        return await read_plan(self.session, await get_plan(self.session, version))

    async def edit(self, version: int, edit: PlanEdit) -> PlanRead:
        base = await require_current_plan(self.session)
        if base.version != version:
            raise PlanOutdated(
                f"Plan version {version} is not the current one (version {base.version}); "
                "reload the plan and try again."
            )
        drafts = [_draft(i, r) for i, r in await load_items(self.session, base)]
        included = [d for d in drafts if not d.excluded]
        excluded = [d for d in drafts if d.excluded]
        match edit:
            case ReorderEdit():
                included = await self._reorder(included, edit)
            case RemoveEdit():
                included, excluded = await self._remove(included, excluded, edit.topic_id)
            case MergeEdit():
                included, excluded = await self._merge(included, excluded, edit)
            case TopicEdit():
                included, excluded = await self._revise(included, excluded, edit)
            case IncludeEdit():
                included, excluded = await self._include(included, excluded, edit.topic_id)
        plan = await write_plan(
            self.session,
            created_by="edit",
            profile_version=base.profile_version,
            source_ids=base.source_ids,
            included=included,
            excluded=excluded,
            curation_run_id=base.curation_run_id,
        )
        await self.session.commit()
        log.info("plan version %d written by %s edit", plan.version, edit.op)
        return await read_plan(self.session, plan)

    @staticmethod
    def _find(drafts: Sequence[ItemDraft], topic_id: uuid.UUID) -> ItemDraft | None:
        return next((d for d in drafts if d.topic_id == topic_id), None)

    async def _reorder(self, included: list[ItemDraft], edit: ReorderEdit) -> list[ItemDraft]:
        order = [str(t) for t in edit.order]
        if sorted(order) != sorted(str(d.topic_id) for d in included):
            raise InvalidEdit("The new order must list every topic of the plan exactly once.")
        edges = _edges(included)
        violations = check_order(order, edges)
        if violations:
            titles = {str(d.topic_id): d.title for d in included}
            raise PrerequisiteViolation(_violation_message(violations, titles))
        by_key = {str(d.topic_id): d for d in included}
        return await repack(self.session, [by_key[k] for k in order], edges)

    async def _remove(
        self, included: list[ItemDraft], excluded: list[ItemDraft], topic_id: uuid.UUID
    ) -> tuple[list[ItemDraft], list[ItemDraft]]:
        target = self._find(included, topic_id)
        if target is None:
            raise TopicNotInPlan(f"Topic {topic_id} is not in the plan.")
        rest = [d for d in included if d.topic_id != topic_id]
        removed = replace(
            target,
            manually_included=False,
            prerequisites=[],
            rationale=f"{REMOVED_NOTE} {target.rationale or ''}".strip(),
        )
        return await repack(self.session, rest, _edges(rest)), [removed, *excluded]

    async def _merge(
        self, included: list[ItemDraft], excluded: list[ItemDraft], edit: MergeEdit
    ) -> tuple[list[ItemDraft], list[ItemDraft]]:
        loser = self._find([*included, *excluded], edit.topic_id)
        survivor = self._find([*included, *excluded], edit.into_topic_id)
        if loser is None or survivor is None:
            missing = edit.topic_id if loser is None else edit.into_topic_id
            raise TopicNotInPlan(f"Topic {missing} is not in the plan.")
        if loser.topic_id == survivor.topic_id:
            raise InvalidEdit("A topic cannot be merged into itself.")
        await merge_topics(self.session, loser.topic_id, survivor.topic_id)
        loser_key, survivor_key = str(loser.topic_id), str(survivor.topic_id)

        def repoint(d: ItemDraft) -> ItemDraft:
            prerequisites = {survivor_key if p == loser_key else p for p in d.prerequisites}
            if d.topic_id == survivor.topic_id:
                prerequisites |= set(loser.prerequisites)
            prerequisites.discard(str(d.topic_id))
            prerequisites.discard(loser_key)
            return replace(d, prerequisites=sorted(prerequisites))

        if survivor.excluded and not loser.excluded:
            # The survivor takes the loser's place in the plan.
            promoted = replace(survivor, excluded=False)
            included = [promoted if d.topic_id == loser.topic_id else d for d in included]
            excluded = [d for d in excluded if d.topic_id != survivor.topic_id]
        else:
            included = [d for d in included if d.topic_id != loser.topic_id]
            excluded = [d for d in excluded if d.topic_id != loser.topic_id]
        included = [repoint(d) for d in included]
        edges = _edges(included)
        by_key = {str(d.topic_id): d for d in included}
        order = stable_order([str(d.topic_id) for d in included], edges)
        return await repack(self.session, [by_key[k] for k in order], edges), excluded

    async def _revise(
        self, included: list[ItemDraft], excluded: list[ItemDraft], edit: TopicEdit
    ) -> tuple[list[ItemDraft], list[ItemDraft]]:
        target = self._find([*included, *excluded], edit.topic_id)
        if target is None:
            raise TopicNotInPlan(f"Topic {edit.topic_id} is not in the plan.")
        current = await current_revision(self.session, target.topic_id)
        title = " ".join(edit.title.split()) if edit.title is not None else current.title
        summary = edit.summary.strip() if edit.summary is not None else current.summary
        if title == current.title and summary == current.summary:
            raise InvalidEdit("Nothing changed: the title and summary are the same.")
        [vector] = await asyncio.to_thread(embeddings.embed_texts, [f"{title}\n{summary}"])
        revision = await add_revision(
            self.session,
            target.topic_id,
            title=title,
            summary=summary,
            created_by="edit",
            embedding=vector,
        )

        def revised(d: ItemDraft) -> ItemDraft:
            if d.topic_id != target.topic_id:
                return d
            return replace(d, topic_revision_id=revision.id, title=revision.title)

        return [revised(d) for d in included], [revised(d) for d in excluded]

    async def _include(
        self, included: list[ItemDraft], excluded: list[ItemDraft], topic_id: uuid.UUID
    ) -> tuple[list[ItemDraft], list[ItemDraft]]:
        target = self._find(excluded, topic_id)
        if target is None:
            raise TopicNotInPlan(f"Topic {topic_id} is not in the excluded list.")
        rationale = (target.rationale or "").removeprefix(REMOVED_NOTE).strip() or None
        added = replace(target, excluded=False, manually_included=True, rationale=rationale)
        profile = await get_profile(self.session)
        targets = await list_target_skills(self.session, profile.id) if profile else []

        def plan_topic(d: ItemDraft) -> PlanTopic:
            return PlanTopic(
                key=str(d.topic_id),
                estimate_minutes=d.estimate_minutes or 0,
                relevance=d.relevance or 0.0,
                priority_rank=priority_rank(d.serves_targets, targets),
                title=d.title,
            )

        topics = {str(d.topic_id): plan_topic(d) for d in included}
        order = list(topics)
        edges = _edges([*included, added])
        at = insert_position(order, plan_topic(added), edges, lambda k: sort_key(topics[k]))
        ordered = [*included[:at], added, *included[at:]]
        rest = [d for d in excluded if d.topic_id != topic_id]
        return await repack(self.session, ordered, _edges(ordered)), rest
