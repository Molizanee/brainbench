"""The ``curation`` job (design D6): material in, a new study plan version out.

The handler runs the steps in a fixed order; agents only fill in each step's structured answer:

1. topic extraction per source window (``topic_extraction``), with chunk citations;
2. embedding dedupe plus one ``topic_merge`` call over the colliding groups;
3. reconcile against existing topics (deterministic; committed so topic ids are stable);
4. scoring in batches against the profile and session history (``scoring``), then the
   threshold filter that splits kept and excluded topics;
5. prerequisites over the kept topics (``prerequisites``), cycles broken by dropping the
   lowest-confidence edge, each drop recorded in the run log;
6. deterministic planning into a new immutable ``study_plans`` version, then target promotion.

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
from app.db.schema import CurationRun, TargetSkill
from app.jobs.errors import PermanentJobError
from app.jobs.registry import JobContext, register
from app.services import curation as service
from app.services import embeddings, providers
from app.services import plans as plan_service
from app.services import search as search_service
from app.services.planner import Edge, break_cycles
from app.services.profile import get_profile, list_target_skills
from app.services.progress import mastered_topic_ids, session_history

__lazy_modules__ = {
    "brainbench_agents.curation",
    "brainbench_providers.strands_models",
    "strands",
    "strands.event_loop._retry",
}
from strands import Agent
from strands.event_loop._retry import ModelRetryStrategy

from brainbench_agents.curation import (
    PREREQUISITES,
    SCORING,
    TOPIC_EXTRACTION,
    TOPIC_MERGE,
    MergeCandidate,
    Prerequisites,
    TopicExtraction,
    TopicMerge,
    TopicScore,
    TopicScoring,
    WindowChunk,
    extraction_prompt,
    make_get_profile,
    make_get_sessions,
    make_search_chunks,
    merge_prompt,
    prerequisites_agent,
    prerequisites_prompt,
    run_structured,
    scoring_agent,
    scoring_prompt,
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
        # Prompt key (T1, T2, ...) -> topic id, for the scoring tools.
        self._topic_keys: dict[str, uuid.UUID] = {}

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
            case "prerequisites":
                agent = prerequisites_agent(routed.model, **common)
            case "scoring":
                agent = scoring_agent(
                    routed.model,
                    get_profile=make_get_profile(self._profile),
                    get_sessions=make_get_sessions(self._sessions),
                    search_chunks=make_search_chunks(self._search),
                    **common,
                )
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

    async def _profile(self) -> dict[str, Any]:
        return await service.profile_snapshot(self.session)

    async def _sessions(self, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        ids = {key: self._topic_keys[key] for key in keys if key in self._topic_keys}
        history = await session_history(self.session, ids.values())
        return {key: service.session_summaries(history[topic_id]) for key, topic_id in ids.items()}

    # --- Pipeline -------------------------------------------------------------------------

    async def run(self) -> None:
        run = await self._load_run()
        await self._inputs(run)
        candidates = await self._extract()
        merged = await self._merge(candidates)
        topics = await self._reconcile(run, merged)
        scores = await self._score(topics)
        threshold = float(self.state["inputs"]["threshold"])
        kept = [t for t, s in zip(topics, scores, strict=True) if s.relevance >= threshold]
        edges = await self._prerequisites(kept)
        await self.ctx.progress("building the study plan")
        self.ctx.raise_if_cancelled()
        plan = await plan_service.write_run_plan(
            self.session,
            run,
            [
                plan_service.RunTopic(
                    topic_id=t.topic_id,
                    revision_id=t.revision_id,
                    title=t.title,
                    relevance=s.relevance,
                    serves_targets=s.serves_targets,
                    rationale=s.rationale,
                    estimate_minutes=s.estimate_minutes,
                )
                for t, s in zip(topics, scores, strict=True)
            ],
            edges,
        )
        self.log_step("planning", f"study plan version {plan.version} written")
        run.finished_at = datetime.now(UTC)
        self._write_run(run)
        await self.save()
        log.info("curation run %s finished with plan version %d", run.id, plan.version)

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

    async def _score(self, topics: list[service.ReconciledTopic]) -> list[service.ScoredTopic]:
        """Score every topic in batches; each finished batch is checkpointed."""
        done: dict[str, dict[str, Any]] = self.state.setdefault("scores", {})
        keyed = {f"T{n}": t for n, t in enumerate(topics, start=1)}
        self._topic_keys = {key: t.topic_id for key, t in keyed.items()}
        pending = [key for key, t in keyed.items() if str(t.topic_id) not in done]
        if pending:
            profile = await service.profile_snapshot(self.session)
            targets = await self._targets()
            history = await session_history(self.session, [keyed[k].topic_id for k in pending])
            mastered = await mastered_topic_ids(self.session)
            size = max(1, settings.curation_scoring_batch)
            for start in range(0, len(pending), size):
                batch = pending[start : start + size]
                await self.ctx.progress(
                    f"scoring topics {start + 1}-{start + len(batch)} of {len(pending)}"
                )
                prompt = scoring_prompt(
                    profile,
                    [
                        {
                            "key": key,
                            "title": keyed[key].title,
                            "summary": keyed[key].summary,
                            "mastered": keyed[key].topic_id in mastered,
                            "sessions": service.session_summaries(history[keyed[key].topic_id]),
                        }
                        for key in batch
                    ],
                )
                answer = await self.call(SCORING, TopicScoring, prompt)
                by_key: dict[str, TopicScore] = {}
                for score in answer.scores:
                    by_key.setdefault(score.topic.strip(), score)
                for key in batch:
                    result = service.apply_score(
                        keyed[key].topic_id,
                        by_key.get(key),
                        targets,
                        default_estimate=settings.curation_default_estimate_minutes,
                    )
                    done[str(result.topic_id)] = result.to_json()
                await self.save()
        scored = [service.ScoredTopic.from_json(done[str(t.topic_id)]) for t in topics]
        threshold = float(self.state["inputs"]["threshold"])
        kept = sum(s.relevance >= threshold for s in scored)
        self.log_step(
            SCORING,
            f"kept {kept} of {len(scored)} topics; excluded {len(scored) - kept} "
            f"below threshold {threshold}",
        )
        return scored

    async def _targets(self) -> list[TargetSkill]:
        profile = await get_profile(self.session)
        return [] if profile is None else await list_target_skills(self.session, profile.id)

    async def _prerequisites(self, kept: list[service.ReconciledTopic]) -> list[Edge]:
        """Prerequisite edges between kept topics, made acyclic; checkpointed as one step."""
        if "edges" in self.state:
            return [Edge(**e) for e in self.state["edges"]]
        edges: list[Edge] = []
        if len(kept) > 1:
            await self.ctx.progress("ordering topics by prerequisites")
            keyed = {f"T{n}": t for n, t in enumerate(kept, start=1)}
            prompt = prerequisites_prompt(
                [{"key": k, "title": t.title, "summary": t.summary} for k, t in keyed.items()]
            )
            answer = await self.call(PREREQUISITES, Prerequisites, prompt)
            proposed = [
                Edge(
                    topic=str(keyed[e.topic.strip()].topic_id),
                    requires=str(keyed[e.requires.strip()].topic_id),
                    confidence=e.confidence,
                )
                for e in answer.edges
                if e.topic.strip() in keyed and e.requires.strip() in keyed
            ]
            edges, dropped = break_cycles({str(t.topic_id) for t in kept}, proposed)
            titles = {str(t.topic_id): t.title for t in kept}
            for drop in dropped:
                self.log_step(
                    PREREQUISITES,
                    f"cycle broken: dropped '{titles[drop.edge.topic]} requires "
                    f"{titles[drop.edge.requires]}' (confidence {drop.edge.confidence})",
                    dropped={
                        "topic": drop.edge.topic,
                        "requires": drop.edge.requires,
                        "confidence": drop.edge.confidence,
                    },
                    cycle=[titles[k] for k in drop.cycle],
                )
        self.log_step(PREREQUISITES, f"{len(edges)} prerequisite relations")
        self.state["edges"] = [
            {"topic": e.topic, "requires": e.requires, "confidence": e.confidence} for e in edges
        ]
        await self.save()
        return edges
