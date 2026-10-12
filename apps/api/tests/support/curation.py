"""Shared curation test helpers: seeding, a job context, fake embeddings, a scripted model.

Imported as ``tests.support.curation`` (``apps/api`` is on ``sys.path``).
"""

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import Chunk, CurationRun, CurrentSkill, Profile, Source, TargetSkill
from app.jobs.registry import HANDLERS, JobContext
from app.services import curation as curation_service
from app.services import embeddings
from app.services.curation import RoutedModel
from app.services.profile import normalize_name
from app.services.topics import normalize_title
from brainbench_agents.curation import (
    MergedTopic,
    PrerequisiteEdge,
    Prerequisites,
    TopicExtraction,
    TopicMerge,
    TopicScore,
    TopicScoring,
)
from brainbench_agents.curation.models import ExtractedTopic
from brainbench_agents.testing import ModelCall, Reply, ScriptedModel, Structured

DIM = 384


async def seed_profile(
    session: AsyncSession,
    *,
    budget_hours: float = 5.0,
    targets: list[tuple[str, str, str]] | None = None,
    skills: list[tuple[str, str]] | None = None,
) -> Profile:
    """A profile with targets ``(name, level, priority)`` and current skills ``(name, level)``."""
    profile = Profile(display_name="Dev", weekly_budget_hours=budget_hours, version=1)
    session.add(profile)
    await session.flush()
    for name, level, priority in (
        [("Distributed consensus", "proficient", "high")] if targets is None else targets
    ):
        session.add(
            TargetSkill(
                profile_id=profile.id,
                name=name,
                name_normalized=normalize_name(name),
                target_level=level,
                priority=priority,
            )
        )
    for name, level in skills or []:
        session.add(
            CurrentSkill(
                profile_id=profile.id,
                name=name,
                name_normalized=normalize_name(name),
                level=level,
            )
        )
    await session.commit()
    return profile


async def seed_source(
    session: AsyncSession, title: str, texts: list[str], *, status: str = "ready"
) -> tuple[Source, list[Chunk]]:
    """A source with one chunk per text (``Title: summary`` texts drive :class:`Script`)."""
    source = Source(
        type="note",
        title=title,
        status=status,
        chunk_count=len(texts),
        created_at=datetime.now(UTC),  # sub-second order; SQLite's now() has 1 s resolution
    )
    session.add(source)
    await session.flush()
    chunks = [
        Chunk(source_id=source.id, position=i, text=text, location={})
        for i, text in enumerate(texts)
    ]
    session.add_all(chunks)
    await session.commit()
    return source, chunks


@dataclass
class Checkpoints:
    saved: list[dict[str, Any]] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)

    async def save(self, checkpoint: dict[str, Any]) -> None:
        self.saved.append(json.loads(json.dumps(checkpoint)))

    async def report(self, message: str) -> None:
        self.progress.append(message)


def make_ctx(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    checkpoint: dict[str, Any] | None = None,
    recorder: Checkpoints | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> JobContext:
    recorder = recorder or Checkpoints()
    return JobContext(
        job_id=uuid.uuid4(),
        type="curation",
        payload={"run_id": str(run_id)},
        checkpoint=json.loads(json.dumps(checkpoint or {})),
        session=session,
        save_checkpoint_fn=recorder.save,
        progress_fn=recorder.report,
        is_cancelled=cancelled,
    )


class FakeEmbeddings:
    """Deterministic embeddings: one axis per concept, so cosine is 1 or 0.

    A text's concept is its first line, normalized, unless ``aliases`` maps that title to a
    shared concept (two titles for one idea).
    """

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self._axes: dict[str, int] = {}

    def alias(self, title: str, concept: str) -> None:
        self.aliases[normalize_title(title)] = normalize_title(concept)

    def concept(self, text: str) -> str:
        key = normalize_title(text.split("\n", 1)[0])
        return self.aliases.get(key, key)

    def __call__(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            axis = self._axes.setdefault(self.concept(text), len(self._axes) % DIM)
            vector = [0.0] * DIM
            vector[axis] = 1.0
            out.append(vector)
        return out


def install_fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> FakeEmbeddings:
    fake = FakeEmbeddings()
    monkeypatch.setattr(embeddings, "embed_texts", fake)
    return fake


_CHUNK_LINE = re.compile(r"^\[(C\d+)\]")


@dataclass
class Script:
    """Scripted answers for every curation step, derived from the prompts.

    Extraction emits one topic per chunk whose text reads ``Title: summary``. Merge names each
    group after its first member (or ``merge_titles[first member title]``). Scoring uses
    ``relevance``/``estimates``/``serves``/``held`` keyed by title. Prerequisites emits
    ``edges`` given as ``(topic title, required title, confidence)``.
    """

    merge_titles: dict[str, str] = field(default_factory=dict)
    summaries: dict[str, str] = field(default_factory=dict)
    relevance: dict[str, float] = field(default_factory=dict)
    estimates: dict[str, int] = field(default_factory=dict)
    serves: dict[str, list[str]] = field(default_factory=dict)
    held: set[str] = field(default_factory=set)
    edges: list[tuple[str, str, float]] = field(default_factory=list)
    default_target: str = "Distributed consensus"
    failures: list[BaseException] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    def __call__(self, call: ModelCall) -> Reply:
        if self.failures:
            return self.failures.pop(0)
        names = set(call.tool_names)
        if "TopicExtraction" in names:
            self.steps.append("topic_extraction")
            return Structured(self._extract(call.prompt))
        if "TopicMerge" in names:
            self.steps.append("topic_merge")
            return Structured(self._merge(call.prompt))
        if "TopicScoring" in names:
            self.steps.append("scoring")
            return Structured(self._score(call.prompt))
        if "Prerequisites" in names:
            self.steps.append("prerequisites")
            return Structured(self._prerequisites(call.prompt))
        raise AssertionError(f"unexpected call with tools {call.tool_names}")

    def _extract(self, prompt: str) -> TopicExtraction:
        topics = []
        lines = prompt.splitlines()
        for i, line in enumerate(lines):
            match = _CHUNK_LINE.match(line)
            if match and i + 1 < len(lines) and ":" in lines[i + 1]:
                title, summary = lines[i + 1].split(":", 1)
                summary = self.summaries.get(title.strip(), summary.strip())
                topics.append(
                    ExtractedTopic(
                        title=title.strip(), summary=summary, chunk_keys=[match.group(1)]
                    )
                )
        return TopicExtraction(topics=topics)

    def _merge(self, prompt: str) -> TopicMerge:
        merged, group = [], None
        for line in prompt.splitlines():
            if line.startswith("Group "):
                group = line.removeprefix("Group ").rstrip(":")
            elif line.startswith("- ") and group is not None:
                title, summary = line[2:].split(":", 1)
                merged.append(
                    MergedTopic(
                        group=group,
                        title=self.merge_titles.get(title.strip(), title.strip()),
                        summary=summary.strip(),
                    )
                )
                group = None
        return TopicMerge(merged=merged)

    def _score(self, prompt: str) -> TopicScoring:
        topics = json.loads(prompt.split("Topics:\n", 1)[1].rsplit("\n\nScore every topic.", 1)[0])
        return TopicScoring(
            scores=[
                TopicScore(
                    topic=t["key"],
                    relevance=self.relevance.get(t["title"], 0.8),
                    serves_targets=self.serves.get(t["title"], [self.default_target]),
                    rationale=f"{t['title']} serves the target.",
                    already_held=t["title"] in self.held,
                    estimate_minutes=self.estimates.get(t["title"], 60),
                )
                for t in topics
            ]
        )

    def _prerequisites(self, prompt: str) -> Prerequisites:
        keys = {}
        for line in prompt.splitlines():
            if re.match(r"^T\d+: ", line):
                key, title, _ = line.split(": ", 2)
                keys[title] = key
        return Prerequisites(
            edges=[
                PrerequisiteEdge(topic=keys[a], requires=keys[b], confidence=c)
                for a, b, c in self.edges
                if a in keys and b in keys
            ]
        )


def install_script(monkeypatch: pytest.MonkeyPatch, script: Script) -> ScriptedModel:
    """Route every curation step to one scripted model driven by ``script``."""
    model = ScriptedModel(script)

    async def routed(session: AsyncSession, step: str) -> RoutedModel:
        return RoutedModel(model=model, provider="scripted", model_id=f"scripted-{step}")

    monkeypatch.setattr(curation_service, "routed_model", routed)
    return model


async def curate(session: AsyncSession, *, threshold: float = 0.4) -> uuid.UUID:
    """Run the curation handler once (the script must be installed) and commit; returns the
    run id."""
    run = CurationRun(profile_version=0, source_ids=[], threshold=threshold)
    session.add(run)
    await session.commit()
    await HANDLERS["curation"](make_ctx(session, run.id))
    await session.commit()
    return run.id
