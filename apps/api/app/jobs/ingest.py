import logging
from uuid import UUID

from app.jobs.errors import PermanentJobError
from app.jobs.registry import JobContext, register
from app.services.extraction import ExtractionError
from app.services.ingestion import IngestionCancelled, ingest_source

log = logging.getLogger(__name__)


@register("ingest")
async def run(ctx: JobContext) -> None:
    """Extract, chunk, and embed one source (payload ``{"source_id": "<uuid>"}``).

    Extraction failures and cancellation are permanent: the source is already marked failed with
    its reason, and retrying cannot change the outcome.
    """
    try:
        source_id = UUID(str(ctx.payload["source_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"invalid ingest payload: {ctx.payload!r}") from exc
    try:
        await ingest_source(
            ctx.session,
            source_id,
            job_id=ctx.job_id,
            progress=ctx.progress,
            cancelled=ctx.cancelled,
        )
    except ExtractionError as exc:
        raise PermanentJobError(str(exc), reason="extraction-failed") from exc
    except IngestionCancelled as exc:
        raise PermanentJobError("ingestion cancelled", reason="cancelled") from exc
