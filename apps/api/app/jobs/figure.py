import logging

from app.jobs.registry import JobContext, register
from app.services import figure, storyboard

log = logging.getLogger(__name__)


@register("figure")
async def run(ctx: JobContext) -> None:
    """Build an interactive Anatomy figure for a topic.

    Payload: ``{"topic_id", "format": "figure", "feedback"?, "previous_asset_id"?}``. Steps:
    storyboard, figure brief (``skipped`` when not applicable), agent session in the job
    workspace, asset with HTML, PNG preview, build source and provenance.
    """
    await figure.run_figure(ctx, storyboard.VisualDeps())
