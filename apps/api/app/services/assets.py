"""Generated assets on disk and in the database (design D7.4).

Files live under ``$BRAINBENCH_DATA/assets/<topic>/<asset-id>/``; ``assets.files`` stores their
paths relative to the data directory. Only the newest asset per topic and format is current;
older ones stay as history.
"""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.schema import Asset
from app.db.schema import Storyboard as StoryboardRow

__lazy_modules__ = {"brainbench_agents.visual"}
from brainbench_agents.visual import Storyboard

log = logging.getLogger(__name__)


def asset_dir(topic_id: uuid.UUID, asset_id: uuid.UUID) -> Path:
    return settings.assets_dir / str(topic_id) / str(asset_id)


def data_relative(path: Path) -> str:
    return path.resolve().relative_to(settings.brainbench_data.resolve()).as_posix()


def data_path(relative: str) -> Path:
    """Absolute path of a stored file; refuses anything outside ``assets/``."""
    path = (settings.brainbench_data / relative).resolve()
    path.relative_to(settings.assets_dir.resolve())
    return path


def job_dir(job_id: uuid.UUID) -> Path:
    return settings.jobs_dir / str(job_id)


def remove_job_dir(job_id: uuid.UUID) -> None:
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


def save_storyboard(
    session: AsyncSession,
    *,
    topic_id: uuid.UUID,
    job_id: uuid.UUID,
    format: str,
    storyboard: Storyboard,
    figure_brief: dict[str, Any] | None = None,
) -> StoryboardRow:
    row = StoryboardRow(
        id=uuid.uuid4(),
        topic_id=topic_id,
        job_id=job_id,
        format=format,
        scenes=[scene.model_dump() for scene in storyboard.scenes],
        figure_brief=figure_brief,
    )
    session.add(row)
    return row


async def save_asset(
    session: AsyncSession,
    *,
    asset_id: uuid.UUID,
    topic_id: uuid.UUID,
    job_id: uuid.UUID,
    format: str,
    storyboard: Storyboard,
    figure_brief: dict[str, Any] | None = None,
    files: dict[str, Any],
    script: str | None = None,
    provenance: dict[str, Any],
    duration_s: float | None = None,
    previous_asset_id: str | None = None,
    feedback: str | None = None,
) -> Asset:
    """Store the storyboard and the asset, and make the asset the current one of its format."""
    row = save_storyboard(
        session,
        topic_id=topic_id,
        job_id=job_id,
        format=format,
        storyboard=storyboard,
        figure_brief=figure_brief,
    )
    await session.execute(
        update(Asset)
        .where(Asset.topic_id == topic_id, Asset.format == format, Asset.is_current.is_(True))
        .values(is_current=False)
    )
    asset = Asset(
        id=asset_id,
        topic_id=topic_id,
        job_id=job_id,
        storyboard_id=row.id,
        previous_asset_id=uuid.UUID(previous_asset_id) if previous_asset_id else None,
        format=format,
        is_current=True,
        feedback=feedback,
        script=script,
        files=files,
        provenance={"storyboard_title": storyboard.title, **provenance},
        duration_s=duration_s,
    )
    session.add(asset)
    await session.flush()
    log.info("asset %s (%s) stored for topic %s", asset_id, format, topic_id)
    return asset
