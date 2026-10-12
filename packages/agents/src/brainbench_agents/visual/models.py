"""Output models of the visual-generation agents (design D7).

Every agent step ends in one of these Pydantic models through Strands structured output, so the
job handlers never parse prose.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

type Format = Literal["video", "figure"]
type Depth = Literal["intuition-first", "rigor-first"]
type Theme = Literal["dark", "light"]

FORMATS: tuple[Format, ...] = ("video", "figure")
SCENE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
MAX_SCENES = 12


@dataclass(frozen=True)
class ChunkRef:
    """A chunk the storyboard may cite, as the injected search callable returns it."""

    chunk_id: str
    source_title: str
    text: str
    location: dict[str, Any] = field(default_factory=dict)
    score: float | None = None


class StoryboardScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=SCENE_ID_PATTERN,
        description="Short snake_case scene id, unique in the storyboard (e.g. intro, quorum).",
    )
    objective: str = Field(min_length=1, description="What the learner understands after it.")
    narration: str = Field(
        min_length=1, description="Spoken narration, in the learner's language, 1 to 4 sentences."
    )
    visual: str = Field(min_length=1, description="What is on screen and what moves.")
    chunk_ids: list[str] = Field(
        min_length=1, description="Ids of the source chunks this scene is grounded in."
    )


class Storyboard(BaseModel):
    """Ordered scenes for one topic; both formats start from it."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    scenes: list[StoryboardScene] = Field(min_length=1, max_length=MAX_SCENES)

    @model_validator(mode="after")
    def _unique_scene_ids(self) -> Self:
        ids = [scene.id for scene in self.scenes]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"scene ids must be unique; repeated: {', '.join(duplicates)}")
        return self

    @property
    def scene_ids(self) -> list[str]:
        return [scene.id for scene in self.scenes]

    def cited_chunk_ids(self) -> set[str]:
        return {chunk for scene in self.scenes for chunk in scene.chunk_ids}


class TrueNumber(BaseModel):
    """A real quantity the figure must show (size, count, rate, threshold)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: str = Field(min_length=1, description="The number as stated by the source.")
    unit: str | None = None
    chunk_id: str | None = Field(default=None, description="Chunk that states it, if any.")


class FigureBrief(BaseModel):
    """What the figure builds, derived from the storyboard and its cited chunks."""

    model_config = ConfigDict(extra="forbid")

    applicable: bool = Field(
        description="False when the topic has no mechanism, object, or structure to build."
    )
    rationale: str = Field(min_length=1)
    object: str | None = Field(
        default=None, description="The physical object or mechanism that embodies the concept."
    )
    parts: list[str] = Field(default_factory=list, description="Main parts and what each does.")
    true_numbers: list[TrueNumber] = Field(default_factory=list)
    motion: str | None = Field(default=None, description="What moves and what drives it.")
    readout: str | None = Field(default=None, description="The live readout the figure shows.")
    theme: Theme = "dark"

    @model_validator(mode="after")
    def _complete_when_applicable(self) -> Self:
        if self.applicable:
            missing = [
                name
                for name in ("object", "motion", "readout")
                if not (getattr(self, name) or "").strip()
            ]
            if not self.true_numbers:
                missing.append("true_numbers")
            if missing:
                raise ValueError(f"an applicable brief needs {', '.join(missing)}")
        return self


class ManimScript(BaseModel):
    """The full Manim Community Python file (one ``Lesson(Scene)``)."""

    model_config = ConfigDict(extra="forbid")

    script: str = Field(min_length=1, description="The complete Python file, nothing else.")
