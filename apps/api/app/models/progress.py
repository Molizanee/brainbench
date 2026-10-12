"""Learning progress contracts: study sessions, topic status, and the progress summary."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Same values as MASTERY_RATINGS in app/db/schema.py (checked by the tests).
type MasteryRating = Literal["struggled", "understood", "confident", "could-teach"]
type TopicStatus = Literal["not-started", "in-progress", "mastered"]

MAX_SESSION_MINUTES = 24 * 60


class StudySessionCreate(BaseModel):
    minutes: int = Field(gt=0, le=MAX_SESSION_MINUTES, description="Duration in minutes")
    rating: MasteryRating
    note: str | None = Field(default=None, max_length=4000)


class StudySessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    minutes: int
    rating: MasteryRating
    note: str | None
    studied_at: datetime


class TopicProgress(BaseModel):
    topic_id: uuid.UUID
    status: TopicStatus
    minutes_studied: int
    session_count: int


class PromotedTarget(BaseModel):
    """A target skill that was achieved and added to the current skills."""

    id: uuid.UUID
    name: str
    level: str


class SessionLogged(BaseModel):
    session: StudySessionRead
    topic: TopicProgress
    promoted_targets: list[PromotedTarget]


class StatusCounts(BaseModel):
    mastered: int = 0
    in_progress: int = 0
    not_started: int = 0


class TargetTopic(BaseModel):
    topic_id: uuid.UUID
    title: str
    status: TopicStatus
    minutes_studied: int
    estimate_minutes: int | None


class TargetProgress(BaseModel):
    id: uuid.UUID
    name: str
    target_level: str
    priority: str
    achieved_at: datetime | None
    counts: StatusCounts
    minutes_studied: int
    # Sum of the plan's estimates for topics that are not mastered.
    minutes_remaining: int
    topics: list[TargetTopic]


class WeeklyPace(BaseModel):
    week_start: datetime
    week_end: datetime
    minutes_studied: int
    budget_minutes: int | None
    used_pct: float | None


class ProgressRead(BaseModel):
    plan_version: int | None
    profile_version: int | None
    minutes_studied: int
    week: WeeklyPace
    targets: list[TargetProgress]
