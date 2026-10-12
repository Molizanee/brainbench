from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.services.search import MAX_K, MIN_K


class SearchRequest(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    k: int = Field(default=10, ge=MIN_K, le=MAX_K)
    source_ids: list[UUID] | None = None
    topic_id: UUID | None = None


class ChunkHitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    source_id: UUID
    source_title: str
    # {"page": 3} | {"path": "docs/raft.md"} | {"fragment": "#:~:text=..."} | {} for notes
    location: dict[str, Any]
    text: str
    score: float


class SearchResponse(BaseModel):
    hits: list[ChunkHitRead]
