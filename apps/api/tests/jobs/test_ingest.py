import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pymupdf as _pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.schema import Chunk, Job, JobEvent, Source
from app.jobs.errors import PermanentJobError
from app.jobs.registry import HANDLERS, JobContext
from app.services import embeddings, ingestion
from app.services.extraction import Extracted, ExtractionError, Section
from app.services.sources import enqueue_ingest

# pymupdf ships no annotations for most of its API.
pymupdf: Any = _pymupdf

LONG_SENTENCE = ("Raft " * 900).strip() + "."  # 4500 chars, one sentence


@pytest.fixture(autouse=True)
def fake_embeddings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Deterministic 384-dim vectors instead of the ONNX model; records each batch."""
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    batches: list[list[str]] = []

    def embed(texts: list[str]) -> list[list[float]]:
        batches.append(texts)
        return [[float(len(t))] + [0.0] * 383 for t in texts]

    monkeypatch.setattr(embeddings, "embed_texts", embed)
    return batches


def make_pdf(path: Path, pages: list[str]) -> None:
    document = pymupdf.Document()
    for text in pages:
        document.new_page().insert_textbox(pymupdf.Rect(36, 36, 576, 806), text, fontsize=6)
    document.save(path)
    document.close()


async def add_source(session: AsyncSession, **fields: Any) -> tuple[Source, Job]:
    source = Source(id=uuid4(), title="t", **fields)
    session.add(source)
    await session.flush()
    job = await enqueue_ingest(session, source)
    await session.commit()
    return source, job


def context(session: AsyncSession, job: Job, **overrides: Any) -> JobContext:
    values: dict[str, Any] = {
        "job_id": job.id,
        "type": "ingest",
        "payload": dict(job.payload),
        "checkpoint": {},
        "session": session,
    }
    values.update(overrides)
    return JobContext(**values)


async def chunks_of(session: AsyncSession, source_id: UUID) -> list[Chunk]:
    rows = await session.scalars(
        select(Chunk).where(Chunk.source_id == source_id).order_by(Chunk.position)
    )
    return list(rows)


async def source_events(session: AsyncSession, job_id: UUID) -> list[tuple[str, dict[str, Any]]]:
    """The handler's ``sources.updated`` rows, without the ``status`` row ``enqueue`` records."""
    rows = await session.scalars(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.kind == "sources.updated")
        .order_by(JobEvent.id)
    )
    return [(row.kind, row.data) for row in rows]


async def test_handler_is_registered() -> None:
    import app.jobs.handlers  # noqa: F401

    assert "ingest" in HANDLERS


async def test_note_ingested_to_ready_with_events_and_progress(
    session: AsyncSession, fake_embeddings: list[list[str]]
) -> None:
    text = "Raft elects a leader. " * 100 + "\n\n" + LONG_SENTENCE
    source, job = await add_source(session, type="note", note=text)
    messages: list[str] = []

    async def progress(message: str) -> None:
        messages.append(message)

    await HANDLERS["ingest"](context(session, job, progress_fn=progress))

    stored = await session.get(Source, source.id)
    assert stored is not None
    rows = await chunks_of(session, source.id)
    assert (stored.status, stored.error, stored.chunk_count) == ("ready", None, len(rows))
    assert [r.position for r in rows] == list(range(len(rows)))
    assert all(len(r.text) <= 2000 and r.embedding is not None for r in rows)
    continuation = [r for r in rows if r.sentence_continuation]
    assert "".join(r.text for r in continuation) == LONG_SENTENCE
    assert sum(len(batch) for batch in fake_embeddings) == len(rows)

    events = await source_events(session, job.id)
    assert [kind for kind, _ in events] == ["sources.updated"] * 3
    assert [data["status"] for _, data in events] == ["extracting", "embedding", "ready"]
    assert events[-1][1] == {
        "source_id": str(source.id),
        "status": "ready",
        "chunk_count": len(rows),
    }
    assert messages[0] == "extracting text"
    assert messages[-1] == f"embedded {len(rows)} of {len(rows)} chunks"


async def test_pdf_chunks_carry_page_numbers(session: AsyncSession, tmp_path: Path) -> None:
    (tmp_path / "sources").mkdir()
    make_pdf(
        tmp_path / "sources" / "doc.pdf",
        [
            "Leader election starts when a follower times out. " * 40,
            "Log replication appends entries on followers. " * 40,
        ],
    )
    source, job = await add_source(session, type="pdf", file_path="sources/doc.pdf")

    await HANDLERS["ingest"](context(session, job))

    rows = await chunks_of(session, source.id)
    assert len(rows) >= 2
    assert {r.location["page"] for r in rows} == {1, 2}
    assert all(set(r.location) == {"page"} for r in rows)
    assert all("Leader election" in r.text for r in rows if r.location["page"] == 1)
    assert all("Log replication" in r.text for r in rows if r.location["page"] == 2)


async def test_url_chunks_get_text_fragments_and_title(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def extract_url(url: str, *, timeout_s: float) -> Extracted:
        return Extracted([Section("Raft elects a leader.", text_fragments=True)], "Raft  docs")

    monkeypatch.setattr(ingestion, "extract_url", extract_url)
    source, job = await add_source(session, type="url", uri="https://raft.github.io/")

    await HANDLERS["ingest"](context(session, job))

    [row] = await chunks_of(session, source.id)
    assert row.location == {"fragment": "#:~:text=Raft%20elects%20a%20leader."}
    await session.refresh(source)
    assert source.title == "Raft docs"


async def test_git_source_uses_job_workdir_and_cleans_it(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    async def extract_git(url: str, workdir: Path, **limits: Any) -> Extracted:
        seen.append(workdir)
        assert await asyncio.to_thread(workdir.is_dir)
        assert limits == {"timeout_s": 300.0, "max_file_bytes": 1024 * 1024}
        return Extracted([Section("package raft", {"path": "raft.go"})])

    monkeypatch.setattr(ingestion, "extract_git", extract_git)
    source, job = await add_source(session, type="git", uri="https://github.com/etcd-io/raft")

    await HANDLERS["ingest"](context(session, job))

    assert seen == [tmp_path / "jobs" / str(job.id)]
    assert not await asyncio.to_thread(seen[0].exists)
    [row] = await chunks_of(session, source.id)
    assert row.location == {"path": "raft.go"}


async def test_failed_extraction_leaves_zero_chunks(session: AsyncSession, tmp_path: Path) -> None:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "bad.pdf").write_bytes(b"%PDF-1.7 garbage")
    source, job = await add_source(session, type="pdf", file_path="sources/bad.pdf")
    # Chunks from an earlier run must not survive a failed re-ingestion either.
    session.add(Chunk(source_id=source.id, position=0, text="stale", location={"page": 1}))
    await session.commit()

    with pytest.raises(PermanentJobError) as raised:
        await HANDLERS["ingest"](context(session, job))
    assert raised.value.reason == "extraction-failed"

    stored = await session.get(Source, source.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error is not None and "PDF" in stored.error
    assert stored.chunk_count == 0
    assert await chunks_of(session, source.id) == []
    events = await source_events(session, job.id)
    assert events[-1][1]["status"] == "failed"


async def test_failure_after_extraction_leaves_zero_chunks_and_is_retryable(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def flaky(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("onnx crashed")
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", flaky)
    monkeypatch.setattr(settings, "embedding_batch_size", 2)
    source, job = await add_source(session, type="note", note="One. " * 1000)

    with pytest.raises(RuntimeError, match="onnx crashed"):
        await HANDLERS["ingest"](context(session, job))

    stored = await session.get(Source, source.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "Ingestion failed unexpectedly (RuntimeError: onnx crashed)."
    assert await session.scalar(select(func.count()).select_from(Chunk)) == 0


async def test_cancelled_job_fails_the_source_permanently(session: AsyncSession) -> None:
    source, job = await add_source(session, type="note", note="Some text.")

    with pytest.raises(PermanentJobError) as raised:
        await HANDLERS["ingest"](context(session, job, is_cancelled=lambda: True))
    assert raised.value.reason == "cancelled"

    stored = await session.get(Source, source.id)
    assert stored is not None
    assert (stored.status, stored.error) == ("failed", "Ingestion was cancelled.")
    assert await chunks_of(session, source.id) == []


async def test_deleted_source_ends_quietly(session: AsyncSession) -> None:
    source, job = await add_source(session, type="note", note="Some text.")
    await session.delete(source)
    await session.commit()

    await HANDLERS["ingest"](context(session, job))
    assert await session.scalar(select(func.count()).select_from(Chunk)) == 0


async def test_source_deleted_mid_run_ends_quietly(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, job = await add_source(session, type="note", note="Some text.")
    source_id = source.id

    async def delete_then_fail(src: Source, job_id: UUID) -> Extracted:
        await session.delete(src)
        await session.commit()
        raise ExtractionError("source vanished")

    monkeypatch.setattr(ingestion, "extract", delete_then_fail)
    await HANDLERS["ingest"](context(session, job))
    assert await session.get(Source, source_id) is None


async def test_invalid_payload_is_permanent(session: AsyncSession) -> None:
    _, job = await add_source(session, type="note", note="x")
    with pytest.raises(PermanentJobError, match="invalid ingest payload"):
        await HANDLERS["ingest"](context(session, job, payload={"source_id": "nope"}))
