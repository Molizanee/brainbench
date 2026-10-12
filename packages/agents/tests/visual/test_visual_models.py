import pytest
from pydantic import ValidationError

from brainbench_agents.visual import FigureBrief, Storyboard


def scene(id: str, chunks: list[str] | None = None) -> dict[str, object]:
    return {
        "id": id,
        "objective": "o",
        "narration": "n",
        "visual": "v",
        "chunk_ids": ["c1"] if chunks is None else chunks,
    }


def test_storyboard_scene_ids_are_unique_slugs() -> None:
    board = Storyboard.model_validate({"title": "Raft", "scenes": [scene("intro"), scene("vote")]})
    assert board.scene_ids == ["intro", "vote"]
    assert board.cited_chunk_ids() == {"c1"}
    with pytest.raises(ValidationError, match="repeated: intro"):
        Storyboard.model_validate({"title": "Raft", "scenes": [scene("intro"), scene("intro")]})
    with pytest.raises(ValidationError):
        Storyboard.model_validate({"title": "Raft", "scenes": [scene("Intro Scene")]})


def test_every_scene_cites_a_chunk() -> None:
    with pytest.raises(ValidationError):
        Storyboard.model_validate({"title": "Raft", "scenes": [scene("intro", [])]})


def test_applicable_brief_needs_object_numbers_motion_readout() -> None:
    assert not FigureBrief(applicable=False, rationale="definitional").applicable
    with pytest.raises(ValidationError, match="object, motion, readout, true_numbers"):
        FigureBrief(applicable=True, rationale="r")
    brief = FigureBrief.model_validate(
        {
            "applicable": True,
            "rationale": "r",
            "object": "a ballot box per server",
            "true_numbers": [{"name": "cluster size", "value": "5"}],
            "motion": "a slider fails servers",
            "readout": "votes needed",
        }
    )
    assert brief.theme == "dark"
