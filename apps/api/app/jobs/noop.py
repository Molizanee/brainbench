import logging

from app.jobs.registry import JobContext, register

log = logging.getLogger(__name__)


@register("noop")
async def run(ctx: JobContext) -> None:
    """Diagnostic job type: records a checkpoint and a progress message, does nothing else."""
    await ctx.progress("noop running")
    await ctx.save_checkpoint({"done": True})
    log.info("noop job %s finished", ctx.job_id)
