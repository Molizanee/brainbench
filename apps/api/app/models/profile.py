"""Learner profile contracts (learner-profile spec).

Shape validation lives here (types, value sets, time budget > 0). Rules that need the stored
profile (single profile, duplicate skill names, target above current level) live in
``app/services/profile.py``.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

# String-literal mirrors of the value sets in ``app.db.schema`` (a test asserts they match).
SkillLevel = Literal["novice", "working", "proficient", "expert"]
TargetLevel = Literal["working", "proficient", "expert"]
Priority = Literal["high", "medium", "low"]
Depth = Literal["intuition-first", "rigor-first"]
ContentFormat = Literal["video", "figure", "text"]

# Content formats a profile can prefer (spec: {video, figure, text}), in canonical order.
CONTENT_FORMATS: tuple[ContentFormat, ...] = ("video", "figure", "text")

SkillName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Background = Annotated[str, StringConstraints(strip_whitespace=True, max_length=10_000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2_000)]
# BCP 47 style tag: "en", "pt-BR", "zh-Hant".
Language = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"),
]
WeeklyBudgetHours = Annotated[float, Field(gt=0, le=168, allow_inf_nan=False)]

MAX_SKILLS = 200


def _canonical_formats(formats: list[ContentFormat]) -> list[ContentFormat]:
    """Drop repeats and keep the canonical order, so equal sets compare equal."""
    return [f for f in CONTENT_FORMATS if f in formats]


def _blank_to_none(reason: str | None) -> str | None:
    return reason or None


Formats = Annotated[list[ContentFormat], Field(min_length=1), AfterValidator(_canonical_formats)]
OptionalReason = Annotated[Reason | None, AfterValidator(_blank_to_none)]


class Preferences(BaseModel):
    formats: Formats = Field(default_factory=lambda: list(CONTENT_FORMATS))
    depth: Depth = "intuition-first"
    language: Language = "en"


class PreferencesUpdate(BaseModel):
    """Partial preferences: omitted fields keep their stored value."""

    formats: Formats | None = None
    depth: Depth | None = None
    language: Language | None = None


class CurrentSkillCreate(BaseModel):
    name: SkillName
    level: SkillLevel


class CurrentSkillUpdate(BaseModel):
    name: SkillName | None = None
    level: SkillLevel | None = None


class TargetSkillCreate(BaseModel):
    name: SkillName
    target_level: TargetLevel
    priority: Priority
    reason: OptionalReason = None


class TargetSkillUpdate(BaseModel):
    """Partial target: omitted fields are unchanged; ``reason: null`` clears the reason."""

    name: SkillName | None = None
    target_level: TargetLevel | None = None
    priority: Priority | None = None
    reason: OptionalReason = None


class ProfileCreate(BaseModel):
    display_name: DisplayName
    background: Background = ""
    weekly_budget_hours: WeeklyBudgetHours
    preferences: Preferences = Field(default_factory=Preferences)
    current_skills: list[CurrentSkillCreate] = Field(default_factory=list, max_length=MAX_SKILLS)
    target_skills: list[TargetSkillCreate] = Field(default_factory=list, max_length=MAX_SKILLS)


class ProfileUpdate(BaseModel):
    """Partial profile: omitted fields are unchanged. Skills and targets have their own routes."""

    display_name: DisplayName | None = None
    background: Background | None = None
    weekly_budget_hours: WeeklyBudgetHours | None = None
    preferences: PreferencesUpdate | None = None


class CurrentSkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    level: SkillLevel


class TargetSkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    target_level: TargetLevel
    priority: Priority
    reason: str | None
    achieved_at: datetime | None


class ProfileRead(BaseModel):
    id: uuid.UUID
    display_name: str
    background: str
    weekly_budget_hours: float
    preferences: Preferences
    current_skills: list[CurrentSkillRead]
    target_skills: list[TargetSkillRead]
    version: int
    created_at: datetime
    updated_at: datetime
