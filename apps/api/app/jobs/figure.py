import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.registry import JobContext, register, register_claim_gate
from app.services import budget, figure, storyboard

log = logging.getLogger(__name__)


@register("figure")
async def run(ctx: JobContext) -> None:
    """Build an interactive Anatomy figure for a topic.

    Payload: ``{"topic_id", "format": "figure", "feedback"?, "previous_asset_id"?}``. Steps:
    storyboard, figure brief (``skipped`` when not applicable), agent session in the job
    workspace, asset with HTML, PNG preview, build source and provenance.
    """
    await figure.run_figure(ctx, storyboard.VisualDeps())


@register_claim_gate("figure")
async def gate(session: AsyncSession) -> bool:
    """Queued figure jobs wait while the month's render minutes are used up."""
    return await budget.claim_allowed(session)
