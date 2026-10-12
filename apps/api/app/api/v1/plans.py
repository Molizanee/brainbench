from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.plans import PlanEdit, PlanRead, PlanSummary
from app.services.plans import PlanService

router = APIRouter(prefix="/api/plans", tags=["plans"])


def get_plan_service(session: AsyncSession = Depends(get_session)) -> PlanService:
    return PlanService(session)


@router.get("", response_model=list[PlanSummary])
async def list_plans(plans: PlanService = Depends(get_plan_service)) -> list[PlanSummary]:
    return [
        PlanSummary(version=p.version, created_at=p.created_at, created_by=p.created_by)
        for p in await plans.versions()
    ]


@router.get("/current", response_model=PlanRead)
async def current_plan(plans: PlanService = Depends(get_plan_service)) -> PlanRead:
    return await plans.current()


@router.get("/{version}", response_model=PlanRead)
async def plan_version(
    version: int = Path(ge=1), plans: PlanService = Depends(get_plan_service)
) -> PlanRead:
    return await plans.get(version)


@router.post("/{version}/edits", response_model=PlanRead, status_code=status.HTTP_201_CREATED)
async def edit_plan(
    edit: PlanEdit,
    version: int = Path(ge=1),
    plans: PlanService = Depends(get_plan_service),
) -> PlanRead:
    """Apply one edit to the current version; the result is a new version."""
    return await plans.edit(version, edit)
