"""The ``curation`` job (design D6): material in, a new study plan version out.

The handler runs the steps in a fixed order; agents only fill in each step's structured answer:

1. topic extraction per source window (``topic_extraction``), with chunk citations;
2. embedding dedupe plus one ``topic_merge`` call over the colliding groups;
3. reconcile against existing topics (deterministic; committed so topic ids are stable);

Every completed unit of work (a window, a step) is saved to the job checkpoint, so a provider
limit deferral or a retry resumes after it instead of starting over. Payload:
``{"run_id": "<curation_runs.id>"}``.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.schema import CurationRun
from app.jobs.errors import PermanentJobError
from app.jobs.registry import JobContext, register

# Module imports: app.services.providers imports app.jobs, which imports this handler, so
# names are looked up at call time (either module may be the one importing first).
from app.services import curation as service
from app.services import embeddings, providers
from app.services import search as search_service

__lazy_modules__ = {
    "brainbench_agents.curation",
    "brainbench_providers.strands_models",
    "strands",
    "strands.event_loop._retry",
}
from strands import Agent
from strands.event_loop._retry import ModelRetryStrategy

from brainbench_agents.curation import (
    TOPIC_EXTRACTION,
    TOPIC_MERGE,
    MergeCandidate,
    TopicExtraction,
    TopicMerge,
    WindowChunk,
    extraction_prompt,
    make_search_chunks,
    merge_prompt,
    run_structured,
    topic_extraction_agent,
    topic_merge_agent,
)
from brainbench_providers.strands_models import ProviderRetryStrategy

log = logging.getLogger(__name__)

type ModelSource = Callable[[AsyncSession, str], Awaitable[service.RoutedModel]]

MAX_TITLE_CHARS = 120


def _now() -> str:
    return datetime.now(UTC).isoformat()


@register("curation")
async def run(ctx: JobContext) -> None:
    await CurationJob(ctx, models=service.routed_model).run()


class CurationJob:
    """One execution (attempt) of a curation run, resuming from ``ctx.checkpoint``."""

    def __init__(
        self,
        ctx: JobContext,
        *,
        models: ModelSource,
        retry_strategy: ModelRetryStrategy | None = None,
    ) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.models = models
        self.retry_strategy = retry_strategy or ProviderRetryStrategy()
        self.state: dict[str, Any] = dict(ctx.checkpoint or {})
        self.state.setdefault("log", [])
        self.state.setdefault("models", {})
        self._agents: dict[str, Agent] = {}

    # --- Plumbing -------------------------------------------------------------------------

    def log_step(self, step: str, message: str, **data: Any) -> None:
        self.state["log"].append({"at": _now(), "step": step, "message": message, **data})

    def _record_usage(self, step: str, usage: dict[str, int]) -> None:
        self.log_step(step, "usage", **usage)

    async def save(self) -> None:
        await self.ctx.save_checkpoint(self.state)

    def _write_run(self, run: CurationRun) -> None:
        run.step_log = list(self.state["log"])
        run.models = dict(self.state["models"])

    async def _agent(self, step: str) -> Agent:
        if step in self._agents:
            return self._agents[step]
        routed = await self.models(self.session, step)
        self.state["models"][step] = {"provider": routed.provider, "model": routed.model_id}
        common: dict[str, Any] = {
            "usage_sink": self._record_usage,
            "retry_strategy": self.retry_strategy,
        }
        match step:
            case "topic_extraction":
                agent = topic_extraction_agent(
                    routed.model, search_chunks=make_search_chunks(self._search), **common
                )
            case "topic_merge":
                agent = topic_merge_agent(routed.model, **common)
            case _:
                raise ValueError(f"unknown curation step {step!r}")
        self._agents[step] = agent
        return agent

    async def call[T: BaseModel](self, step: str, output_model: type[T], prompt: str) -> T:
        """One agent call; a provider throttle becomes a deferral with the checkpoint saved."""
        self.ctx.raise_if_cancelled()
        agent = await self._agent(step)
        try:
            return await run_structured(agent, output_model, prompt)
        except Exception as exc:
            if providers.find_throttle(exc) is not None:
                await self.save()
                raise providers.provider_limit_from(exc) from exc
            raise

    async def _search(self, query: str, k: int) -> list[dict[str, Any]]:
        source_ids = [uuid.UUID(s) for s in self.state["inputs"]["source_ids"]]
        hits = await search_service.search_chunks(self.session, query, k, source_ids=source_ids)
        return [
            {
                "chunk_id": str(h.chunk_id),
                "source_title": h.source_title,
                "location": h.location,
                "text": h.text,
                "score": round(h.score, 4),
            }
            for h in hits
        ]

    # --- Pipeline -------------------------------------------------------------------------

    async def run(self) -> None:
        run = await self._load_run()
        await self._inputs(run)
        candidates = await self._extract()
        merged = await self._merge(candidates)
        await self._reconcile(run, merged)
        run.finished_at = datetime.now(UTC)
        self._write_run(run)
        await self.save()
        log.info("curation run %s finished", run.id)

    async def _load_run(self) -> CurationRun:
        try:
            run_id = uuid.UUID(str(self.ctx.payload["run_id"]))
        except (KeyError, ValueError) as exc:
            raise PermanentJobError(f"invalid curation payload: {self.ctx.payload!r}") from exc
        run = await self.session.get(CurationRun, run_id)
        if run is None:
            raise PermanentJobError(f"curation run {run_id} does not exist")
        return run

    async def _inputs(self, run: CurationRun) -> None:
        """Snapshot the profile version and ready sources once; resumes reuse the snapshot."""
        if "inputs" not in self.state:
            try:
                inputs = await service.check_preconditions(self.session)
            except Exception as exc:
                message = getattr(exc, "message", str(exc))
                raise PermanentJobError(message, reason="preconditions") from exc
            self.state["inputs"] = {
                "profile_version": inputs.profile_version,
                "source_ids": [str(s) for s in inputs.source_ids],
                "threshold": run.threshold,
            }
            self.log_step(
                "inputs",
                f"{len(inputs.source_ids)} ready sources, profile version "
                f"{inputs.profile_version}, threshold {run.threshold}",
            )
            await self.save()
        run.profile_version = self.state["inputs"]["profile_version"]
        run.source_ids = list(self.state["inputs"]["source_ids"])

    async def _extract(self) -> list[service.Candidate]:
        """Candidate topics from every window of every ready source, in source order."""
        done: dict[str, list[dict[str, Any]]] = self.state.setdefault("windows", {})
        source_ids = [uuid.UUID(s) for s in self.state["inputs"]["source_ids"]]
        candidates: list[service.Candidate] = []
        for number, source_id in enumerate(source_ids, start=1):
            windows = await service.source_windows(
                self.session, source_id, window_chars=settings.curation_window_chars
            )
            for window in windows:
                if window.id not in done:
                    await self.ctx.progress(
                        f"extracting topics: source {number}/{len(source_ids)}, "
                        f"window {window.index + 1}/{len(windows)}"
                    )
                    done[window.id] = [c.to_json() for c in await self._extract_window(window)]
                    await self.save()
                candidates.extend(service.Candidate.from_json(c) for c in done[window.id])
        self.log_step(TOPIC_EXTRACTION, f"{len(candidates)} candidate topics")
        return candidates

    async def _extract_window(self, window: service.SourceWindow) -> list[service.Candidate]:
        prompt = extraction_prompt(
            window.source_title,
            [WindowChunk(c.key, c.text, service.location_label(c.location)) for c in window.chunks],
        )
        answer = await self.call(TOPIC_EXTRACTION, TopicExtraction, prompt)
        by_key = {c.key: str(c.chunk_id) for c in window.chunks}
        out: list[service.Candidate] = []
        for topic in answer.topics:
            title = " ".join(topic.title.split())[:MAX_TITLE_CHARS]
            summary = topic.summary.strip()
            cited = list(dict.fromkeys(by_key[k] for k in topic.chunk_keys if k in by_key))
            if title and summary and cited:
                out.append(service.Candidate(title=title, summary=summary, chunk_ids=cited))
        return out

    async def _merge(self, candidates: list[service.Candidate]) -> list[service.Candidate]:
        """Fold near-duplicates (embedding or title) into one topic each."""
        if "candidates" in self.state:
            return [service.Candidate.from_json(c) for c in self.state["candidates"]]
        await self.ctx.progress("merging duplicate topics")
        vectors = await asyncio.to_thread(
            embeddings.embed_texts, [c.embedding_text for c in candidates]
        )
        groups = service.cluster_candidates(
            candidates, vectors, similarity=settings.curation_merge_similarity
        )
        collisions = {f"G{n}": g for n, g in enumerate((g for g in groups if len(g) > 1), 1)}
        merged: dict[str, service.Candidate] = {}
        if collisions:
            prompt = merge_prompt(
                {
                    key: [MergeCandidate(candidates[i].title, candidates[i].summary) for i in g]
                    for key, g in collisions.items()
                }
            )
            answer = await self.call(TOPIC_MERGE, TopicMerge, prompt)
            for item in answer.merged:
                members = collisions.get(item.group)
                title = " ".join(item.title.split())[:MAX_TITLE_CHARS]
                if members is None or item.group in merged or not title or not item.summary:
                    continue
                merged[item.group] = service.Candidate(
                    title=title,
                    summary=item.summary.strip(),
                    chunk_ids=service.union_chunks([candidates[i] for i in members]),
                )
        group_keys = {tuple(g): key for key, g in collisions.items()}
        out: list[service.Candidate] = []
        for group in groups:
            if len(group) == 1:
                out.append(candidates[group[0]])
                continue
            key = group_keys[tuple(group)]
            out.append(merged.get(key) or service.fallback_merge([candidates[i] for i in group]))
        self.log_step(
            TOPIC_MERGE,
            f"{len(candidates)} candidates became {len(out)} topics "
            f"({len(collisions)} duplicate groups merged)",
        )
        self.state["candidates"] = [c.to_json() for c in out]
        await self.save()
        return out

    async def _reconcile(
        self, run: CurationRun, candidates: list[service.Candidate]
    ) -> list[service.ReconciledTopic]:
        """Reuse existing topic ids; committed at once so a later deferral keeps them."""
        if "topics" in self.state:
            return [service.ReconciledTopic.from_json(t) for t in self.state["topics"]]
        await self.ctx.progress("matching topics to earlier runs")
        vectors = await asyncio.to_thread(
            embeddings.embed_texts, [c.embedding_text for c in candidates]
        )
        topics = await service.reconcile(
            self.session,
            run.id,
            candidates,
            vectors,
            similarity=settings.curation_merge_similarity,
        )
        created = sum(t.created for t in topics)
        revised = sum(t.revised for t in topics)
        self.log_step(
            "reconcile",
            f"{len(topics)} topics: {created} new, {revised} revised, "
            f"{len(topics) - created - revised} unchanged",
        )
        self.state["topics"] = [t.to_json() for t in topics]
        self._write_run(run)
        await self.session.commit()
        await self.save()
        return topics
