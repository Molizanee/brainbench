"""Tutor chat API (tutor-chat spec, design D10).

``POST /api/tutor/threads/{id}/messages`` answers with its own ``text/event-stream`` (named
events ``delta``, ``citation``, ``related_topic``, ``done``, ``error``, ``limit``), separate from
the app-wide ``/events`` stream.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, get_session
from app.models.tutor import (
    TutorMessageCreate,
    TutorMessageRead,
    TutorStopRead,
    TutorThreadList,
    TutorThreadRead,
)
from app.services.providers import ProviderRuntime, get_provider_runtime
from app.services.tutor import (
    ProviderTutorModels,
    TutorModels,
    TutorRuns,
    TutorService,
    parse_context,
    process_runs,
    reply_stream,
)

router = APIRouter(prefix="/api/tutor", tags=["tutor"])

CONTEXT_HELP = '"general", "plan", or "topic:<topic id>"'


def get_tutor_sessions() -> async_sessionmaker[AsyncSession]:
    """Sessions for the reply task, which outlives the request."""
    return SessionFactory


def get_tutor_runs() -> TutorRuns:
    return process_runs()


def get_tutor_models(runtime: ProviderRuntime = Depends(get_provider_runtime)) -> TutorModels:
    return ProviderTutorModels(runtime)


def get_tutor_service(
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_tutor_sessions),
    runs: TutorRuns = Depends(get_tutor_runs),
    models: TutorModels = Depends(get_tutor_models),
) -> TutorService:
    return TutorService(session, sessions=sessions, runs=runs, models=models)


@router.get("/threads", response_model=TutorThreadList)
async def list_threads(
    context: str = Query("general", description=CONTEXT_HELP),
    svc: TutorService = Depends(get_tutor_service),
) -> TutorThreadList:
    return await svc.list_threads(parse_context(context))


@router.post("/threads", response_model=TutorThreadRead, status_code=status.HTTP_201_CREATED)
async def create_thread(
    context: str = Query("general", description=CONTEXT_HELP),
    svc: TutorService = Depends(get_tutor_service),
) -> TutorThreadRead:
    return await svc.create_thread(parse_context(context))


@router.get("/threads/{thread_id}/messages", response_model=list[TutorMessageRead])
async def list_messages(
    thread_id: uuid.UUID, svc: TutorService = Depends(get_tutor_service)
) -> list[TutorMessageRead]:
    return await svc.messages(thread_id)


@router.post("/threads/{thread_id}/messages", response_class=StreamingResponse)
async def send_message(
    thread_id: uuid.UUID,
    body: TutorMessageCreate,
    svc: TutorService = Depends(get_tutor_service),
) -> StreamingResponse:
    run = await svc.send(thread_id, body.content)
    return StreamingResponse(
        reply_stream(run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/threads/{thread_id}/stop", response_model=TutorStopRead)
async def stop_reply(
    thread_id: uuid.UUID, svc: TutorService = Depends(get_tutor_service)
) -> TutorStopRead:
    return await svc.stop(thread_id)


@router.get("/sources/{source_id}/file", response_class=FileResponse)
async def source_file(
    source_id: uuid.UUID, svc: TutorService = Depends(get_tutor_service)
) -> FileResponse:
    """The stored PDF behind a citation (``#page=n`` in the citation link opens the page)."""
    path = await svc.source_file(source_id)
    return FileResponse(
        path, media_type="application/pdf", filename=path.name, content_disposition_type="inline"
    )
