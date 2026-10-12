"""``GET /events``: the app's single server-sent event stream (authenticated by the cookie).

Unnamed SSE messages whose data is ``{"type": "<prefix>.<event>", "payload": ...}``. For each
committed job event it sends ``jobs.updated`` (payload: the job, as ``JobRead``) followed by
``jobs.event`` (payload: the event row, as ``JobEventRead``). A comment line keeps idle
connections open; client disconnects end the generator and release the subscription.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from functools import cache

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import SessionFactory
from app.models.jobs import JobEventRead, JobRead
from app.services.job_events import EventHub, load_notification

router = APIRouter(tags=["events"])

KEEPALIVE_S = 15.0
RETRY_MS = 3000


@cache
def _process_hub() -> EventHub:
    # Its own unpooled engine: the LISTEN connection is held for as long as anyone is listening.
    return EventHub(create_async_engine(settings.database_url, poolclass=NullPool))


def get_event_hub() -> EventHub:
    return _process_hub()


def get_event_sessions() -> async_sessionmaker[AsyncSession]:
    return SessionFactory


def sse_message(type: str, payload: BaseModel) -> str:
    data = json.dumps({"type": type, "payload": payload.model_dump(mode="json")})
    return f"data: {data}\n\n"


async def event_stream(
    request: Request, hub: EventHub, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[str]:
    yield f"retry: {RETRY_MS}\n\n"
    async with hub.subscribe() as queue:
        while True:
            try:
                notification = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
            except TimeoutError:
                if await request.is_disconnected():
                    return
                yield ": keepalive\n\n"
                continue
            if notification is None:
                return
            async with sessions() as session:
                loaded = await load_notification(session, notification)
            if loaded is None:
                continue
            job, event = loaded
            yield sse_message("jobs.updated", JobRead.model_validate(job))
            yield sse_message("jobs.event", JobEventRead.model_validate(event))


@router.get("/events", response_class=StreamingResponse)
async def events(
    request: Request,
    hub: EventHub = Depends(get_event_hub),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_event_sessions),
) -> StreamingResponse:
    return StreamingResponse(
        event_stream(request, hub, sessions),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
