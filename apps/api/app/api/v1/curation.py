from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.curation import CurationRunCreate, CurationRunRead
from app.services.curation import CurationService

router = APIRouter(prefix="/api/curation", tags=["curation"])


def get_curation_service(session: AsyncSession = Depends(get_session)) -> CurationService:
    return CurationService(session)


@router.post("/runs", response_model=CurationRunRead, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    body: CurationRunCreate | None = None,
    curation: CurationService = Depends(get_curation_service),
) -> CurationRunRead:
    return await curation.start(body or CurationRunCreate())


@router.get("/runs", response_model=list[CurationRunRead])
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    curation: CurationService = Depends(get_curation_service),
) -> list[CurationRunRead]:
    return await curation.list_runs(limit=limit)
