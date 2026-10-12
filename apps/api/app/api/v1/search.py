from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.search import ChunkHitRead, SearchRequest, SearchResponse
from app.services.search import search_chunks

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest, session: AsyncSession = Depends(get_session)
) -> SearchResponse:
    hits = await search_chunks(
        session, body.query, body.k, source_ids=body.source_ids, topic_id=body.topic_id
    )
    return SearchResponse(hits=[ChunkHitRead.model_validate(hit) for hit in hits])
