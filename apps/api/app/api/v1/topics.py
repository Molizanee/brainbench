import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.assets import GenerateRequest, GenerateResponse
from app.models.topics import TopicRead
from app.services.generation import GenerationService
from app.services.topic_view import TopicMerged, TopicView

router = APIRouter(prefix="/api/topics", tags=["topics"])


def get_topic_view(session: AsyncSession = Depends(get_session)) -> TopicView:
    return TopicView(session)


def get_generation_service(session: AsyncSession = Depends(get_session)) -> GenerationService:
    return GenerationService(session)


@router.get(
    "/{topic_id}",
    response_model=TopicRead,
    responses={307: {"description": "The topic was merged; follow Location to the survivor."}},
)
async def read_topic(
    topic_id: uuid.UUID, view: TopicView = Depends(get_topic_view)
) -> TopicRead | Response:
    try:
        return await view.read(topic_id)
    except TopicMerged as merged:
        return RedirectResponse(
            f"/api/topics/{merged.survivor_id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT
        )


@router.post(
    "/{topic_id}/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_for_topic(
    topic_id: uuid.UUID,
    body: GenerateRequest,
    generation: GenerationService = Depends(get_generation_service),
) -> GenerateResponse:
    return await generation.for_topic(topic_id, body.formats)
