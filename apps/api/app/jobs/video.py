import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.registry import JobContext, register, register_claim_gate
from app.services import budget, storyboard, video

log = logging.getLogger(__name__)


@register("video")
async def run(ctx: JobContext) -> None:
    """Generate a narrated Manim video for a topic.

    Payload: ``{"topic_id", "format": "video", "feedback"?, "previous_asset_id"?}``. Steps:
    storyboard, script, render with up to three attempts (repairs in between), captions, asset.
    """
    await video.run_video(ctx, storyboard.VisualDeps())


@register_claim_gate("video")
async def gate(session: AsyncSession) -> bool:
    """Queued video jobs wait while the month's render minutes are used up."""
    return await budget.claim_allowed(session)
