import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.progress import ProgressRead, SessionLogged, StudySessionCreate
from app.services.progress import ProgressService
from app.services.sessions import SessionService

router = APIRouter(tags=["progress"])


def get_session_service(session: AsyncSession = Depends(get_session)) -> SessionService:
    return SessionService(session)


def get_progress_service(session: AsyncSession = Depends(get_session)) -> ProgressService:
    return ProgressService(session)


@router.post(
    "/api/topics/{topic_id}/sessions",
    response_model=SessionLogged,
    status_code=status.HTTP_201_CREATED,
)
async def log_session(
    topic_id: uuid.UUID,
    body: StudySessionCreate,
    sessions: SessionService = Depends(get_session_service),
) -> SessionLogged:
    return await sessions.log(topic_id, body)


@router.get("/api/progress", response_model=ProgressRead)
async def read_progress(
    utc_offset_minutes: int = Query(
        0, ge=-14 * 60, le=14 * 60, description="Viewer's local offset from UTC for the week"
    ),
    progress: ProgressService = Depends(get_progress_service),
) -> ProgressRead:
    return await progress.summary(utc_offset_minutes=utc_offset_minutes)
