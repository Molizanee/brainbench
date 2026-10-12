from typing import get_args

import pytest
from pydantic import ValidationError

from app.db.schema import MASTERY_RATINGS
from app.models.progress import MAX_SESSION_MINUTES, MasteryRating, StudySessionCreate


def test_ratings_match_the_schema_constraint() -> None:
    assert get_args(MasteryRating.__value__) == MASTERY_RATINGS


@pytest.mark.parametrize("minutes", [0, -1, MAX_SESSION_MINUTES + 1])
def test_minutes_out_of_range_rejected(minutes: int) -> None:
    with pytest.raises(ValidationError) as exc:
        StudySessionCreate(minutes=minutes, rating="understood")
    assert exc.value.errors()[0]["loc"] == ("minutes",)


def test_note_is_optional() -> None:
    assert StudySessionCreate(minutes=1, rating="could-teach").note is None
