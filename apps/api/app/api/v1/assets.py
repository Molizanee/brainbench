import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.topics import get_generation_service
from app.db.session import get_session
from app.models.assets import (
    AssetFileName,
    GenerateResponse,
    PlanGenerateResponse,
    RegenerateRequest,
)
from app.services.generation import GenerationService
from app.services.topic_view import asset_file

router = APIRouter(tags=["assets"])

# Figures are agent-generated pages: they may run their own inline scripts but can reach
# nothing, and the sandbox directive gives them an opaque origin (design D7.4).
FIGURE_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:"
)


@router.post(
    "/api/assets/{asset_id}/regenerate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_asset(
    asset_id: uuid.UUID,
    body: RegenerateRequest,
    generation: GenerationService = Depends(get_generation_service),
) -> GenerateResponse:
    return await generation.regenerate(asset_id, body.feedback)


@router.get("/api/assets/{asset_id}/{name}", response_class=FileResponse)
async def read_asset_file(
    asset_id: uuid.UUID, name: AssetFileName, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    """Serve a generated file; ``Range`` requests are answered with 206 (video seeking)."""
    path, media_type = await asset_file(session, asset_id, name)
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    if name == "figure.html":
        headers["Content-Security-Policy"] = FIGURE_CSP
        headers["Referrer-Policy"] = "no-referrer"
    return FileResponse(path, media_type=media_type, headers=headers)


@router.post("/api/plans/current/generate", response_model=PlanGenerateResponse)
async def generate_for_plan(
    dry_run: bool = Query(False, description="Count the jobs without creating them."),
    generation: GenerationService = Depends(get_generation_service),
) -> PlanGenerateResponse:
    """One job per topic of the latest plan and per preferred visual format, in plan order.
    The web calls it with ``dry_run=true`` first and shows ``job_count`` for confirmation."""
    return await generation.for_plan(dry_run=dry_run)
