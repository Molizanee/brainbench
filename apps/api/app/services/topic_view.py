"""The topic page read model (``GET /api/topics/{id}``, task 7.6) and asset file lookup."""

import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.schema import (
    Asset,
    Chunk,
    Job,
    PlanItem,
    Source,
    StudyPlan,
    Topic,
    TopicRevision,
)
from app.db.schema import Storyboard as StoryboardRow
from app.models.assets import (
    AssetRead,
    AssetUrls,
    BuildSourceFile,
    ChunkCitation,
    SceneRead,
    StoryboardRead,
)
from app.models.jobs import JobRead
from app.models.topics import PlanItemRead, TopicRead, TopicRevisionRead, TopicStatsRead
from app.services import assets, budget, progress
from app.services.generation import FORMATS, AssetNotFound, dedupe_key
from app.services.topics import TopicNotFound, resolve_topic

log = logging.getLogger(__name__)

EXCERPT_CHARS = 280
# Served file name -> (key in ``assets.files``, media type).
ASSET_FILES: dict[str, tuple[str, str]] = {
    "lesson.mp4": ("video", "video/mp4"),
    "lesson.vtt": ("captions", "text/vtt; charset=utf-8"),
    "figure.html": ("html", "text/html; charset=utf-8"),
    "preview.png": ("png", "image/png"),
}
URL_FIELDS = {"video": "video", "captions": "captions", "html": "html", "png": "preview"}


class TopicMerged(Exception):
    """The topic was merged; the router redirects to the survivor."""

    def __init__(self, survivor_id: uuid.UUID) -> None:
        super().__init__(str(survivor_id))
        self.survivor_id = survivor_id


class AssetFileNotFound(NotFound):
    code = "asset-file-not-found"


def asset_urls(asset: Asset) -> AssetUrls:
    urls: dict[str, str] = {}
    for name, (key, _) in ASSET_FILES.items():
        if asset.files.get(key):
            urls[URL_FIELDS[key]] = f"/api/assets/{asset.id}/{name}"
    return AssetUrls.model_validate(urls)


class TopicView:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(self, topic_id: uuid.UUID) -> TopicRead:
        topic = await self.session.get(Topic, topic_id)
        if topic is None:
            raise TopicNotFound(f"Topic {topic_id} does not exist.")
        if topic.merged_into is not None:
            survivor = await resolve_topic(self.session, topic_id)
            raise TopicMerged(survivor.id)
        revisions = list(
            (
                await self.session.execute(
                    select(TopicRevision)
                    .where(TopicRevision.topic_id == topic.id)
                    .order_by(TopicRevision.revision.desc())
                )
            ).scalars()
        )
        if not revisions:
            raise TopicNotFound(f"Topic {topic_id} has no revision.")
        stats = (await progress.topic_stats(self.session, [topic.id]))[topic.id]
        return TopicRead(
            id=topic.id,
            title=revisions[0].title,
            summary=revisions[0].summary,
            status=stats.status,
            stats=TopicStatsRead(minutes_studied=stats.minutes, session_count=stats.sessions),
            revision=revisions[0].revision,
            history=[
                TopicRevisionRead(
                    revision=r.revision,
                    title=r.title,
                    created_by=r.created_by,
                    created_at=r.created_at,
                )
                for r in revisions
            ],
            assets=await self.assets(topic.id),
            plan_item=await self.plan_item(topic.id),
            jobs=await self.latest_jobs(topic.id),
            budget=budget.to_read(await budget.budget_status(self.session)),
        )

    async def assets(self, topic_id: uuid.UUID) -> list[AssetRead]:
        rows = list(
            (
                await self.session.execute(
                    select(Asset)
                    .where(Asset.topic_id == topic_id)
                    .order_by(Asset.created_at.desc(), Asset.id)
                )
            ).scalars()
        )
        board_ids = [a.storyboard_id for a in rows if a.storyboard_id is not None]
        boards = {
            b.id: b
            for b in (
                await self.session.execute(
                    select(StoryboardRow).where(StoryboardRow.id.in_(board_ids))
                )
            ).scalars()
        }
        chunk_ids = {
            chunk
            for board in boards.values()
            for scene in board.scenes
            for chunk in scene.get("chunk_ids", [])
        }
        citations = await self.citations(chunk_ids)
        return [
            self.asset_read(a, boards.get(a.storyboard_id) if a.storyboard_id else None, citations)
            for a in rows
        ]

    async def citations(self, chunk_ids: set[str]) -> dict[str, ChunkCitation]:
        ids = []
        for value in chunk_ids:
            try:
                ids.append(uuid.UUID(value))
            except ValueError:
                continue
        if not ids:
            return {}
        stmt = (
            select(Chunk.id, Chunk.text, Chunk.location, Chunk.source_id, Source.title)
            .join(Source, Source.id == Chunk.source_id)
            .where(Chunk.id.in_(ids))
        )
        found = {}
        for row in await self.session.execute(stmt):
            text = " ".join(row.text.split())
            found[str(row.id)] = ChunkCitation(
                chunk_id=str(row.id),
                source_id=row.source_id,
                source_title=row.title,
                location=dict(row.location or {}),
                excerpt=text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else ""),
            )
        return found

    def asset_read(
        self,
        asset: Asset,
        board: StoryboardRow | None,
        citations: dict[str, ChunkCitation],
    ) -> AssetRead:
        provenance: dict[str, Any] = dict(asset.provenance or {})
        storyboard = None
        if board is not None:
            storyboard = StoryboardRead(
                id=board.id,
                title=str(provenance.get("storyboard_title") or "Storyboard"),
                scenes=[
                    SceneRead(
                        id=str(scene.get("id", "")),
                        objective=str(scene.get("objective", "")),
                        narration=str(scene.get("narration", "")),
                        visual=str(scene.get("visual", "")),
                        chunk_ids=list(scene.get("chunk_ids", [])),
                        citations=[
                            citations.get(c) or ChunkCitation(chunk_id=c)
                            for c in scene.get("chunk_ids", [])
                        ],
                    )
                    for scene in board.scenes
                ],
                figure_brief=board.figure_brief,
                created_at=board.created_at,
            )
        detail_keys = (
            "render_attempts",
            "failed_attempts",
            "missing_sections",
            "sections",
            "stop_reason",
            "turns",
            "timed_out",
        )
        return AssetRead(
            id=asset.id,
            topic_id=asset.topic_id,
            format="video" if asset.format == "video" else "figure",
            is_current=asset.is_current,
            created_at=asset.created_at,
            job_id=asset.job_id,
            previous_asset_id=asset.previous_asset_id,
            feedback=asset.feedback,
            duration_s=asset.duration_s,
            urls=asset_urls(asset),
            storyboard=storyboard,
            script=asset.script,
            build_source=[
                BuildSourceFile.model_validate(entry)
                for entry in provenance.get("build_source", [])
            ],
            transcript_tail=provenance.get("transcript_tail"),
            details={k: provenance[k] for k in detail_keys if k in provenance},
        )

    async def plan_item(self, topic_id: uuid.UUID) -> PlanItemRead | None:
        plan = (
            await self.session.execute(
                select(StudyPlan).order_by(StudyPlan.version.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if plan is None:
            return None
        item = (
            await self.session.execute(
                select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.topic_id == topic_id)
            )
        ).scalar_one_or_none()
        if item is None:
            return None
        return PlanItemRead(
            plan_version=plan.version,
            position=item.position,
            week_start=item.week_start,
            week_span=item.week_span,
            estimate_minutes=item.estimate_minutes,
            relevance=item.relevance,
            rationale=item.rationale,
            excluded=item.excluded,
        )

    async def latest_jobs(self, topic_id: uuid.UUID) -> list[JobRead]:
        latest = []
        for format in FORMATS:
            job = (
                await self.session.execute(
                    select(Job)
                    .where(Job.dedupe_key == dedupe_key(format, topic_id))
                    .order_by(Job.created_at.desc(), Job.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if job is not None:
                latest.append(JobRead.model_validate(job))
        return latest


async def asset_file(session: AsyncSession, asset_id: uuid.UUID, name: str) -> tuple[Path, str]:
    """Path and media type of one of an asset's served files."""
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFound(f"Asset {asset_id} does not exist.")
    key, media_type = ASSET_FILES[name]
    relative = asset.files.get(key)
    if not isinstance(relative, str):
        raise AssetFileNotFound(f"Asset {asset_id} has no {name}.")
    try:
        path = assets.data_path(relative)
    except ValueError as exc:
        raise AssetFileNotFound(f"Asset {asset_id} has no {name}.") from exc
    if not path.is_file():
        log.warning("asset %s file %s is missing on disk", asset_id, relative)
        raise AssetFileNotFound(f"Asset {asset_id} has no {name}.")
    return path, media_type
