from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, TypeAdapter

SourceType = Literal["url", "pdf", "git", "note"]
SourceStatus = Literal["queued", "extracting", "embedding", "ready", "failed"]

TITLE_MAX_LENGTH = 200
NOTE_MAX_LENGTH = 1_000_000


class UrlSourceCreate(BaseModel):
    type: Literal["url"]
    url: HttpUrl


class GitSourceCreate(BaseModel):
    type: Literal["git"]
    url: HttpUrl


class NoteSourceCreate(BaseModel):
    type: Literal["note"]
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=TITLE_MAX_LENGTH)] = (
        ""
    )
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_MAX_LENGTH)
    ]


# JSON body of `POST /api/sources`; PDFs arrive as multipart instead.
SourceCreate = Annotated[
    UrlSourceCreate | GitSourceCreate | NoteSourceCreate, Field(discriminator="type")
]
source_create_adapter: TypeAdapter[UrlSourceCreate | GitSourceCreate | NoteSourceCreate] = (
    TypeAdapter(SourceCreate)
)


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: SourceType
    title: str
    uri: str | None
    status: SourceStatus
    chunk_count: int
    error: str | None
    job_id: UUID | None
    created_at: datetime
    updated_at: datetime
