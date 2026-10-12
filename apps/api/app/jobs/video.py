import logging

from app.jobs.registry import JobContext, register
from app.services import storyboard, video

log = logging.getLogger(__name__)


@register("video")
async def run(ctx: JobContext) -> None:
    """Generate a narrated Manim video for a topic.

    Payload: ``{"topic_id", "format": "video", "feedback"?, "previous_asset_id"?}``. Steps:
    storyboard, script, render with up to three attempts (repairs in between), captions, asset.
    """
    await video.run_video(ctx, storyboard.VisualDeps())
