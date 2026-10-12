"""Tutor chat (tutor-chat spec, design D10): threads per context, messages, and streamed replies.

A reply is not a job: it runs in the API process under a dedicated semaphore (one in flight,
``settings.tutor_concurrency``) as a :class:`TutorRun`. The run owns an asyncio task that
retrieves passages, drives the Strands tutor agent, and persists the reply; the agent callback
pushes each text delta onto the run's queue, which the HTTP layer drains as named SSE events
(``delta``, ``citation``, ``related_topic``, ``done``, ``error``, ``limit``). Stopping sets the
agent's cancel signal (the adapter closes the provider stream on its next event) and, after
``tutor_stop_grace_s``, cancels the agent task outright; the partial reply is saved with
``stopped``. A client disconnect stops the run the same way. The run persists through its own
session, so a cancelled HTTP stream never interrupts the write.
"""

import asyncio
import json
import logging
import math
import re
import threading
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import Conflict, DomainError, NotFound
from app.db.schema import (
    PlanItem,
    Source,
    TopicRevision,
    TutorMessage,
    TutorThread,
)
from app.models.tutor import (
    CitationRead,
    DoneEvent,
    ErrorEvent,
    Interruption,
    LimitEvent,
    RelatedTopicRead,
    TutorContextRead,
    TutorMessageRead,
    TutorStopRead,
    TutorThreadList,
    TutorThreadRead,
)
from app.services import embeddings
from app.services.progress import current_plan
from app.services.providers import (
    ProviderRuntime,
    ensure_provider_for,
    find_throttle,
    model_for,
)
from app.services.tutor_tools import (
    build_tutor_tools,
    current_asset_formats,
    latest_revisions,
    load_context,
    resolve_topic,
)
from brainbench_agents.tutor import (
    ChunkRef,
    CitationLedger,
    TutorContext,
    TutorTools,
    build_turn_prompt,
    make_tutor_agent,
    retrieve,
)

__lazy_modules__ = {"brainbench_providers.strands_models", "strands.models.model"}
from strands.models.model import Model

from brainbench_providers.strands_models import ProviderRetryStrategy

log = logging.getLogger(__name__)

TASK_CLASS = "tutoring"
TITLE_CHARS = 80
EXCERPT_CHARS = 280
RELATED_TEXT_CHARS = 2000
# Interactive replies retry a throttle once, briefly; a longer limit is reported to the panel.
RETRY_OPTIONS = {"max_attempts": 2, "initial_delay": 1, "max_delay": 4}

_LABELS = re.compile(r"\[[^\[\]\n]{1,300}\]")


# --- Errors -------------------------------------------------------------------------------


class InvalidContext(DomainError):
    code = "invalid-context"


class ThreadNotFound(NotFound):
    code = "thread-not-found"


class SourceFileNotFound(NotFound):
    code = "source-file-not-found"


class TutorBusy(Conflict):
    code = "tutor-busy"


# --- Contexts -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextRef:
    kind: str
    topic_id: uuid.UUID | None = None


def parse_context(value: str | None) -> ContextRef:
    """``general`` (default), ``plan``, or ``topic:<uuid>``."""
    text = (value or "general").strip()
    if text in {"general", "plan"}:
        return ContextRef(text)
    kind, sep, raw = text.partition(":")
    if kind == "topic" and sep:
        try:
            return ContextRef("topic", uuid.UUID(raw))
        except ValueError:
            pass
    raise InvalidContext('context must be "general", "plan", or "topic:<topic id>"')


# --- Conversions --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def message_read(message: TutorMessage) -> TutorMessageRead:
    usage = message.usage or {}
    return TutorMessageRead(
        id=message.id,
        thread_id=message.thread_id,
        role=message.role,
        content=message.content,
        citations=[CitationRead.model_validate(c) for c in message.citations or []],
        related_topics=[RelatedTopicRead.model_validate(r) for r in message.related_topics or []],
        stopped=message.stopped,
        interrupted=usage.get("interrupted"),
        created_at=message.created_at,
    )


_BLOB_PATHS = {"github.com": "/blob/HEAD/", "gitlab.com": "/-/blob/HEAD/"}


def citation_href(ref: ChunkRef) -> str | None:
    """Where a citation chip links: the PDF page, the web passage (text fragment), the file in
    the repository; notes have no location to open."""
    location = ref.location
    if ref.source_type == "pdf":
        page = location.get("page")
        url = f"/api/tutor/sources/{ref.source_id}/file"
        return f"{url}#page={page}" if page is not None else url
    if ref.source_type == "url" and ref.uri:
        return ref.uri.split("#", 1)[0] + str(location.get("fragment") or "")
    if ref.source_type == "git" and ref.uri:
        repo = ref.uri.removesuffix("/").removesuffix(".git")
        blob = _BLOB_PATHS.get(urlsplit(repo).hostname or "")
        path = location.get("path")
        if blob and path:
            return f"{repo}{blob}{quote(str(path))}"
        return repo
    return None


def citation_read(ref: ChunkRef) -> CitationRead:
    return CitationRead(
        chunk_id=uuid.UUID(ref.chunk_id),
        source_id=uuid.UUID(ref.source_id),
        source_title=ref.source_title,
        source_type=ref.source_type,
        location=ref.location,
        label=ref.label,
        href=citation_href(ref),
        excerpt=" ".join(ref.text.split())[:EXCERPT_CHARS],
    )


def history_messages(messages: Sequence[TutorMessage]) -> list[dict[str, Any]]:
    """Thread history as Strands messages: alternating roles starting with the engineer,
    ending with a reply (an unanswered question at the end is dropped)."""
    turns: list[dict[str, Any]] = []
    for message in messages:
        if not message.content.strip():
            continue
        if turns and turns[-1]["role"] == message.role:
            turns[-1]["content"][0]["text"] += "\n\n" + message.content
            continue
        if not turns and message.role != "user":
            continue
        turns.append({"role": message.role, "content": [{"text": message.content}]})
    while turns and turns[-1]["role"] == "user":
        turns.pop()
    return turns


# --- Related topics -----------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def related_topics(
    session: AsyncSession,
    text: str,
    *,
    exclude: uuid.UUID | None = None,
    threshold: float | None = None,
    limit: int | None = None,
) -> list[RelatedTopicRead]:
    """Topics of the current plan whose revision embedding is within ``threshold`` cosine
    similarity of the reply (deterministic, no LLM call)."""
    plan = await current_plan(session)
    if plan is None:
        return []
    rows = await session.execute(
        select(PlanItem.topic_id, TopicRevision.title, TopicRevision.embedding)
        .join(TopicRevision, TopicRevision.id == PlanItem.topic_revision_id)
        .where(
            PlanItem.plan_id == plan.id,
            PlanItem.excluded.is_(False),
            TopicRevision.embedding.is_not(None),
        )
        .order_by(PlanItem.position)
    )
    candidates = [
        (tid, title, emb) for tid, title, emb in rows if tid != exclude and emb is not None
    ]
    if not candidates:
        return []
    body = _LABELS.sub("", text)[:RELATED_TEXT_CHARS]
    (vector,) = await asyncio.to_thread(embeddings.embed_texts, [body])
    floor = settings.tutor_related_threshold if threshold is None else threshold
    scored = sorted(
        (
            (score, tid, title)
            for tid, title, emb in candidates
            if (score := cosine(vector, [float(x) for x in emb])) >= floor
        ),
        key=lambda entry: -entry[0],
    )[: limit or settings.tutor_related_max]
    assets = await current_asset_formats(session, [tid for _, tid, _ in scored])
    return [
        RelatedTopicRead(topic_id=tid, title=title, score=round(score, 4), assets=assets[tid])
        for score, tid, title in scored
    ]


# --- Models -------------------------------------------------------------------------------


class TutorModels(Protocol):
    """Provider access for the tutor: the pre-send check and the routed model."""

    async def ensure(self, session: AsyncSession) -> None: ...

    async def model(self, session: AsyncSession) -> Model: ...


@dataclass
class ProviderTutorModels:
    runtime: ProviderRuntime

    async def ensure(self, session: AsyncSession) -> None:
        await ensure_provider_for(session, TASK_CLASS, self.runtime)

    async def model(self, session: AsyncSession) -> Model:
        return await model_for(session, TASK_CLASS, self.runtime)


# --- Runs ---------------------------------------------------------------------------------

type QueueItem = tuple[str, dict[str, Any]] | None


class TutorRun:
    """One reply in flight: the agent task, its cancel signal, and the event queue."""

    def __init__(self, thread_id: uuid.UUID, user_message_id: uuid.UUID) -> None:
        self.thread_id = thread_id
        self.user_message_id = user_message_id
        self.cancel = threading.Event()
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.parts: list[str] = []
        self.ledger = CitationLedger()
        self.citations: list[CitationRead] = []
        self.interrupted: Interruption | None = None
        self.finished = asyncio.Event()
        self.message: TutorMessageRead | None = None
        self.task: asyncio.Task[None] | None = None
        self.agent_task: asyncio.Task[Any] | None = None
        self._boundary = False

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        self.queue.put_nowait((event, data))

    def on_agent_event(self, **event: Any) -> None:
        """Strands callback: forward text deltas (``data``) and recognize citations."""
        if self.cancel.is_set():
            return
        chunk = event.get("data")
        if isinstance(chunk, str) and chunk:
            if self._boundary and self.parts and not self.text.endswith("\n"):
                chunk = "\n\n" + chunk
            self._boundary = False
            self.parts.append(chunk)
            self.emit("delta", {"text": chunk})
            if "]" in chunk:
                for ref in self.ledger.scan(self.text):
                    citation = citation_read(ref)
                    self.citations.append(citation)
                    self.emit("citation", citation.model_dump(mode="json"))
        elif "message" in event:
            # A model turn ended (tool call or answer): separate the next turn's text.
            self._boundary = True

    def stop(self, reason: Interruption, grace_s: float | None = None) -> None:
        """Cancel the reply: the agent stops at its next streamed event, and the agent task is
        cancelled outright after the grace period if the provider is silent."""
        if self.finished.is_set() or self.cancel.is_set():
            return
        self.interrupted = reason
        self.cancel.set()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        delay = settings.tutor_stop_grace_s if grace_s is None else grace_s
        loop.call_later(delay, self._force_stop)

    def _force_stop(self) -> None:
        if self.agent_task is not None and not self.agent_task.done():
            log.info("tutor reply on thread %s did not stop in time; cancelling", self.thread_id)
            self.agent_task.cancel()


class TutorRuns:
    """Process-wide registry of in-flight replies behind the tutor semaphore."""

    def __init__(self, limit: int = 1) -> None:
        self._semaphore = asyncio.Semaphore(limit)
        self._runs: dict[uuid.UUID, TutorRun] = {}

    def get(self, thread_id: uuid.UUID) -> TutorRun | None:
        return self._runs.get(thread_id)

    async def acquire(self) -> None:
        """Take the slot without waiting; ``TutorBusy`` (409) while another reply streams."""
        if self._semaphore.locked():
            raise TutorBusy("The tutor is still answering. Stop that reply or wait for it.")
        await self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()

    def start(self, run: TutorRun, work: Callable[[TutorRun], Any]) -> None:
        self._runs[run.thread_id] = run
        run.task = asyncio.create_task(work(run), name=f"tutor-{run.thread_id}")

    def finish(self, run: TutorRun) -> None:
        if self._runs.get(run.thread_id) is run:
            del self._runs[run.thread_id]
        self.release()


@cache
def process_runs() -> TutorRuns:
    return TutorRuns(settings.tutor_concurrency)


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def reply_stream(run: TutorRun, keepalive_s: float | None = None) -> AsyncGenerator[str]:
    """The SSE body of ``POST /api/tutor/threads/{id}/messages``. Closing it early (client
    disconnect) stops the reply, which is then saved as stopped."""
    interval = settings.tutor_keepalive_s if keepalive_s is None else keepalive_s
    try:
        yield ": reply\n\n"
        while True:
            try:
                item = await asyncio.wait_for(run.queue.get(), interval)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                return
            yield sse(*item)
    finally:
        if not run.finished.is_set():
            run.stop("disconnect")


# --- Service ------------------------------------------------------------------------------


class TutorService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        sessions: async_sessionmaker[AsyncSession],
        runs: TutorRuns,
        models: TutorModels,
        tools_factory: Callable[[async_sessionmaker[AsyncSession]], TutorTools] = (
            build_tutor_tools
        ),
    ) -> None:
        self.session = session
        self.sessions = sessions
        self.runs = runs
        self.models = models
        self.tools_factory = tools_factory

    # threads ------------------------------------------------------------------------------

    async def _context(self, ref: ContextRef) -> tuple[ContextRef, TutorContextRead]:
        if ref.kind == "topic" and ref.topic_id is not None:
            topic = await resolve_topic(self.session, ref.topic_id)
            revision = (await latest_revisions(self.session, [topic.id])).get(topic.id)
            title = revision.title if revision else None
            assets = (await current_asset_formats(self.session, [topic.id]))[topic.id]
            read = TutorContextRead(
                type="topic", topic_id=topic.id, title=title, label=title or "Topic", assets=assets
            )
            return ContextRef("topic", topic.id), read
        if ref.kind == "plan":
            return ref, TutorContextRead(type="plan", label="Study plan")
        return ref, TutorContextRead(type="general", label="General")

    async def list_threads(self, ref: ContextRef) -> TutorThreadList:
        ref, context = await self._context(ref)
        statement = select(TutorThread).where(TutorThread.context_type == ref.kind)
        if ref.topic_id is not None:
            statement = statement.where(TutorThread.context_id == ref.topic_id)
        threads = await self.session.scalars(
            statement.order_by(TutorThread.updated_at.desc(), TutorThread.created_at.desc())
        )
        return TutorThreadList(
            context=context, threads=[TutorThreadRead.model_validate(t) for t in threads]
        )

    async def create_thread(self, ref: ContextRef) -> TutorThreadRead:
        ref, _ = await self._context(ref)
        now = _now()
        thread = TutorThread(
            context_type=ref.kind, context_id=ref.topic_id, created_at=now, updated_at=now
        )
        self.session.add(thread)
        await self.session.commit()
        log.info("tutor thread %s created for %s", thread.id, ref.kind)
        return TutorThreadRead.model_validate(thread)

    async def _thread(self, thread_id: uuid.UUID) -> TutorThread:
        thread = await self.session.get(TutorThread, thread_id)
        if thread is None:
            raise ThreadNotFound(f"Tutor thread {thread_id} does not exist.")
        return thread

    async def _messages(self, thread_id: uuid.UUID) -> list[TutorMessage]:
        rows = await self.session.scalars(
            select(TutorMessage)
            .where(TutorMessage.thread_id == thread_id)
            .order_by(TutorMessage.created_at, TutorMessage.role.desc())
        )
        return list(rows)

    async def messages(self, thread_id: uuid.UUID) -> list[TutorMessageRead]:
        await self._thread(thread_id)
        return [message_read(m) for m in await self._messages(thread_id)]

    async def source_file(self, source_id: uuid.UUID) -> Path:
        """The stored PDF of a source, for citation links to a page."""
        source = await self.session.get(Source, source_id)
        if source is None or source.type != "pdf" or not source.file_path:
            raise SourceFileNotFound("This source has no stored file.")
        root = settings.brainbench_data.resolve()
        path = (root / source.file_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise SourceFileNotFound("This source has no stored file.")
        return path

    # replies ------------------------------------------------------------------------------

    async def send(self, thread_id: uuid.UUID, content: str) -> TutorRun:
        """Accept a question and start its reply. Raises before anything is stored when the
        thread is unknown (404), no provider can serve tutoring (400 ``no-provider``), the
        vault is locked (409), or another reply is streaming (409 ``tutor-busy``)."""
        thread = await self._thread(thread_id)
        await self.models.ensure(self.session)
        await self.runs.acquire()
        try:
            model = await self.models.model(self.session)
            context = await load_context(self.session, thread.context_type, thread.context_id)
            history = history_messages(
                (await self._messages(thread.id))[-settings.tutor_history_messages :]
            )
            now = _now()
            question = TutorMessage(
                thread_id=thread.id, role="user", content=content, created_at=now
            )
            self.session.add(question)
            if not thread.title:
                thread.title = " ".join(content.split())[:TITLE_CHARS]
            thread.updated_at = now
            await self.session.commit()
        except BaseException:
            self.runs.release()
            raise
        run = TutorRun(thread.id, question.id)
        log.info("tutor reply started on thread %s", thread.id)
        self.runs.start(
            run,
            lambda r: self._execute(
                r, model=model, context=context, history=history, question=content
            ),
        )
        return run

    async def stop(self, thread_id: uuid.UUID) -> TutorStopRead:
        await self._thread(thread_id)
        run = self.runs.get(thread_id)
        if run is None:
            replies = [m for m in await self._messages(thread_id) if m.role == "assistant"]
            return TutorStopRead(
                stopped=False, message=message_read(replies[-1]) if replies else None
            )
        run.stop("stop")
        try:
            await asyncio.wait_for(
                asyncio.shield(run.finished.wait()), settings.tutor_stop_grace_s + 5
            )
        except TimeoutError:
            log.warning("tutor reply on thread %s still finishing after stop", thread_id)
        return TutorStopRead(stopped=True, message=run.message)

    async def _execute(
        self,
        run: TutorRun,
        *,
        model: Model,
        context: TutorContext,
        history: list[dict[str, Any]],
        question: str,
    ) -> None:
        failure: tuple[str, dict[str, Any]] | None = None
        usage: dict[str, Any] = {}
        try:
            tools = self.tools_factory(self.sessions)
            refs = await retrieve(tools, context, question)
            run.ledger.add(refs)
            agent = make_tutor_agent(
                model,
                context,
                tools,
                ledger=run.ledger,
                messages=history,  # type: ignore[arg-type]
                callback_handler=run.on_agent_event,
                retry_strategy=ProviderRetryStrategy(**RETRY_OPTIONS),
            )
            run.agent_task = asyncio.create_task(
                agent.invoke_async(build_turn_prompt(question, refs), cancel_signal=run.cancel)
            )
            await asyncio.wait([run.agent_task])
            try:
                if not run.agent_task.cancelled():
                    run.agent_task.result()
            finally:
                totals = agent.event_loop_metrics.accumulated_usage
                usage = {
                    "input_tokens": int(totals.get("inputTokens", 0)),
                    "output_tokens": int(totals.get("outputTokens", 0)),
                }
        except Exception as exc:
            throttle = find_throttle(exc)
            if throttle is not None:
                reset_at = getattr(throttle, "reset_at", None)
                scope = getattr(throttle, "scope", None)
                log.info("tutor reply on thread %s hit a provider limit", run.thread_id)
                failure = (
                    "limit",
                    {
                        "reset_at": reset_at,
                        "scope": scope,
                        "message": "The provider's usage limit is reached."
                        + (" It resets at the time shown." if reset_at else ""),
                    },
                )
            else:
                log.exception("tutor reply on thread %s failed", run.thread_id)
                failure = (
                    "error",
                    {"code": "tutor-failed", "message": f"The tutor failed: {type(exc).__name__}"},
                )
        try:
            await self._finish(run, context=context, usage=usage, failure=failure, model=model)
        except Exception:
            log.exception("saving the tutor reply on thread %s failed", run.thread_id)
            run.emit("error", {"code": "tutor-failed", "message": "The reply could not be saved."})
        finally:
            run.queue.put_nowait(None)
            run.finished.set()
            self.runs.finish(run)

    async def _finish(
        self,
        run: TutorRun,
        *,
        context: TutorContext,
        usage: dict[str, Any],
        failure: tuple[str, dict[str, Any]] | None,
        model: Model,
    ) -> None:
        text = run.text
        stopped = run.cancel.is_set()
        related: list[RelatedTopicRead] = []
        if failure is None and not stopped and text.strip():
            async with self.sessions() as session:
                try:
                    related = await related_topics(
                        session,
                        text,
                        exclude=uuid.UUID(context.topic_id) if context.topic_id else None,
                    )
                except Exception:
                    log.exception("related topics for thread %s failed", run.thread_id)
            for topic in related:
                run.emit("related_topic", topic.model_dump(mode="json"))
        interrupted: str | None = failure[0] if failure else (run.interrupted if stopped else None)
        config = model.get_config()
        if isinstance(config, dict) and config.get("model_id"):
            usage["model"] = config["model_id"]
        if interrupted:
            usage["interrupted"] = interrupted
        async with self.sessions() as session:
            thread = await session.get(TutorThread, run.thread_id)
            saved: TutorMessage | None = None
            if thread is None:
                pass
            elif failure is not None and not text.strip():
                # Nothing was answered: withdraw the question so it can be sent again.
                await session.execute(
                    delete(TutorMessage).where(TutorMessage.id == run.user_message_id)
                )
            else:
                saved = TutorMessage(
                    thread_id=run.thread_id,
                    role="assistant",
                    content=text,
                    citations=[c.model_dump(mode="json") for c in run.citations],
                    related_topics=[r.model_dump(mode="json") for r in related],
                    stopped=stopped or failure is not None,
                    usage=usage,
                    created_at=_now(),
                )
                session.add(saved)
                thread.updated_at = _now()
            await session.commit()
            run.message = message_read(saved) if saved is not None else None
        log.info(
            "tutor reply on thread %s %s (%d chars, %d citations)",
            run.thread_id,
            interrupted or "done",
            len(text),
            len(run.citations),
        )
        if failure is not None:
            kind, data = failure
            kept = run.user_message_id if run.message is not None else None
            payload = (
                LimitEvent(reply=run.message, user_message_id=kept, **data)
                if kind == "limit"
                else ErrorEvent(reply=run.message, user_message_id=kept, **data)
            )
            run.emit(kind, payload.model_dump(mode="json"))
        elif run.message is not None:
            done = DoneEvent(message=run.message, user_message_id=run.user_message_id)
            run.emit("done", done.model_dump(mode="json"))
