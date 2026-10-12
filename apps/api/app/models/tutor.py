"""Tutor chat contracts (tutor-chat spec, design D10)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

type TutorContextType = Literal["topic", "plan", "general"]
type TutorRole = Literal["user", "assistant"]
type Interruption = Literal["stop", "disconnect", "limit", "error"]

MESSAGE_MAX_CHARS = 8000


class TutorContextRead(BaseModel):
    """The context a thread is bound to; ``label`` is what the panel's context chip shows."""

    type: TutorContextType
    topic_id: uuid.UUID | None = None
    title: str | None = None
    label: str
    assets: list[str] = []


class TutorThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    context_type: TutorContextType
    context_id: uuid.UUID | None
    title: str | None
    created_at: datetime
    updated_at: datetime


class TutorThreadList(BaseModel):
    """Threads of one context, most recently active first."""

    context: TutorContextRead
    threads: list[TutorThreadRead]


class CitationRead(BaseModel):
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    source_title: str
    source_type: str | None = None
    location: dict[str, Any]
    label: str
    href: str | None
    excerpt: str


class RelatedTopicRead(BaseModel):
    topic_id: uuid.UUID
    title: str
    score: float
    assets: list[str] = []


class TutorMessageRead(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    role: TutorRole
    content: str
    citations: list[CitationRead]
    related_topics: list[RelatedTopicRead]
    stopped: bool
    interrupted: Interruption | None = None
    created_at: datetime


class TutorMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=MESSAGE_MAX_CHARS)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Write a question first")
        return value


class TutorStopRead(BaseModel):
    """``stopped`` is false when no reply was in flight; ``message`` is the thread's last reply."""

    stopped: bool
    message: TutorMessageRead | None


class LimitEvent(BaseModel):
    """``reply`` is the partial answer saved as stopped; with no text, the question is withdrawn
    (``user_message_id`` null) so it can be sent again."""

    reset_at: datetime | None
    scope: str | None
    message: str
    reply: TutorMessageRead | None = None
    user_message_id: uuid.UUID | None = None


class ErrorEvent(BaseModel):
    code: str
    message: str
    reply: TutorMessageRead | None = None
    user_message_id: uuid.UUID | None = None


class DoneEvent(BaseModel):
    message: TutorMessageRead
    user_message_id: uuid.UUID
