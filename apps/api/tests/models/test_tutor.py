from typing import get_args

import pytest
from pydantic import ValidationError

from app.db.schema import TUTOR_CONTEXTS, TUTOR_ROLES
from app.models.tutor import (
    MESSAGE_MAX_CHARS,
    TutorContextType,
    TutorMessageCreate,
    TutorRole,
)


def test_literals_match_the_schema() -> None:
    assert get_args(TutorContextType.__value__) == TUTOR_CONTEXTS
    assert get_args(TutorRole.__value__) == TUTOR_ROLES


def test_message_is_trimmed_and_bounded() -> None:
    assert TutorMessageCreate(content="  Why Raft?\n").content == "Why Raft?"
    for bad in ("", "   ", "x" * (MESSAGE_MAX_CHARS + 1)):
        with pytest.raises(ValidationError):
            TutorMessageCreate(content=bad)
