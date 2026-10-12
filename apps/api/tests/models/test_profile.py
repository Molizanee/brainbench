from typing import get_args

import pytest
from pydantic import ValidationError

from app.db.schema import DEPTHS, PRIORITIES, SKILL_LEVELS, TARGET_LEVELS
from app.models.profile import (
    Depth,
    Preferences,
    PreferencesUpdate,
    Priority,
    SkillLevel,
    TargetLevel,
    TargetSkillCreate,
)


def test_literals_mirror_schema_value_sets() -> None:
    assert get_args(SkillLevel) == SKILL_LEVELS
    assert get_args(TargetLevel) == TARGET_LEVELS
    assert get_args(Priority) == PRIORITIES
    assert get_args(Depth) == DEPTHS


def test_formats_are_deduplicated_in_canonical_order() -> None:
    prefs = Preferences.model_validate({"formats": ["text", "video", "text"]})
    assert prefs.formats == ["video", "text"]
    assert PreferencesUpdate.model_validate({"formats": ["figure", "video"]}).formats == [
        "video",
        "figure",
    ]


def test_formats_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        Preferences.model_validate({"formats": []})


@pytest.mark.parametrize("language", ["en", "pt-BR", "zh-Hant"])
def test_language_tags_accepted(language: str) -> None:
    assert Preferences(language=language).language == language


@pytest.mark.parametrize("language", ["", "english!", "e"])
def test_language_tags_rejected(language: str) -> None:
    with pytest.raises(ValidationError):
        Preferences(language=language)


def test_blank_reason_becomes_none() -> None:
    target = TargetSkillCreate(name="x", target_level="working", priority="low", reason="  ")
    assert target.reason is None
