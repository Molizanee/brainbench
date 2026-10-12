from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.jobs import JobDetail, JobEventRead, JobRead, JobStatus
from app.services.jobs import JobService

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def get_job_service(session: AsyncSession = Depends(get_session)) -> JobService:
    return JobService(session)


@router.get("", response_model=list[JobRead])
async def list_jobs(
    status: list[JobStatus] | None = Query(None),
    type: str | None = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=200),
    jobs: JobService = Depends(get_job_service),
) -> list[JobRead]:
    found = await jobs.list_jobs(statuses=status, type=type, limit=limit)
    return [JobRead.model_validate(job) for job in found]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: UUID, jobs: JobService = Depends(get_job_service)) -> JobDetail:
    job, events = await jobs.get_job(job_id)
    return JobDetail(
        **JobRead.model_validate(job).model_dump(),
        events=[JobEventRead.model_validate(event) for event in events],
    )


@router.post("/{job_id}/cancel", response_model=JobRead)
async def cancel_job(job_id: UUID, jobs: JobService = Depends(get_job_service)) -> JobRead:
    return JobRead.model_validate(await jobs.cancel(job_id))
