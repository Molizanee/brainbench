"""Ingestion pipeline run by the ``ingest`` job: extract, chunk, embed, store (design D5).

Status moves ``queued`` → ``extracting`` → ``embedding`` → ``ready``, or to ``failed`` with a
human-readable ``error``. Chunks are written in the same transaction that sets ``ready``, so a
failure at any step leaves no chunks for the source. Every status change commits and writes a
``sources.updated`` job event.
"""

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.schema import Chunk, JobEvent, Source
from app.models.sources import TITLE_MAX_LENGTH
from app.services import embeddings
from app.services.chunker import chunk_text
from app.services.chunks import NewChunk, replace_chunks
from app.services.extraction import (
    Extracted,
    ExtractionError,
    extract_git,
    extract_note,
    extract_pdf,
    extract_url,
    text_fragment,
)

log = logging.getLogger(__name__)

SOURCES_UPDATED = "sources.updated"

type Progress = Callable[[str], Awaitable[None]]
type Cancelled = Callable[[], bool]


class IngestionCancelled(Exception):
    pass


def build_chunks(extracted: Extracted) -> list[NewChunk]:
    """Chunk every section in order; positions run across the whole source."""
    chunks: list[NewChunk] = []
    for section in extracted.sections:
        for piece in chunk_text(section.text):
            location = dict(section.location)
            if section.text_fragments:
                location["fragment"] = text_fragment(piece.text)
            chunks.append(NewChunk(len(chunks), piece.text, location, piece.sentence_continuation))
    return chunks


async def extract(source: Source, job_id: UUID) -> Extracted:
    match source.type:
        case "url":
            assert source.uri is not None
            return await asyncio.to_thread(
                extract_url, source.uri, timeout_s=settings.url_fetch_timeout_s
            )
        case "pdf":
            assert source.file_path is not None
            return await asyncio.to_thread(extract_pdf, settings.brainbench_data / source.file_path)
        case "git":
            assert source.uri is not None
            workdir = settings.jobs_dir / str(job_id)
            workdir.mkdir(parents=True, exist_ok=True)
            try:
                return await extract_git(
                    source.uri,
                    workdir,
                    timeout_s=settings.git_clone_timeout_s,
                    max_file_bytes=settings.git_file_max_bytes,
                )
            finally:
                await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)
        case "note":
            return extract_note(source.note or "")
    raise ExtractionError(f"Unknown source type {source.type!r}.")


async def _publish(session: AsyncSession, source: Source, job_id: UUID) -> None:
    """Commit the source's state and write the ``sources.updated`` event with it."""
    session.add(
        JobEvent(
            job_id=job_id,
            kind=SOURCES_UPDATED,
            message=source.error,
            data={
                "source_id": str(source.id),
                "status": source.status,
                "chunk_count": source.chunk_count,
            },
        )
    )
    await session.commit()


async def _set_status(session: AsyncSession, source: Source, status: str, job_id: UUID) -> None:
    source.status = status
    source.error = None
    await _publish(session, source, job_id)


async def _embed(
    chunks: list[NewChunk], progress: Progress, cancelled: Cancelled
) -> list[list[float]]:
    vectors: list[list[float]] = []
    batch = settings.embedding_batch_size
    for start in range(0, len(chunks), batch):
        if cancelled():
            raise IngestionCancelled
        texts = [chunk.text for chunk in chunks[start : start + batch]]
        vectors.extend(await asyncio.to_thread(embeddings.embed_texts, texts))
        await progress(f"embedded {len(vectors)} of {len(chunks)} chunks")
    return vectors


async def ingest_source(
    session: AsyncSession,
    source_id: UUID,
    *,
    job_id: UUID,
    progress: Progress,
    cancelled: Cancelled,
) -> None:
    """Run the whole pipeline for one source.

    Raises ``ExtractionError`` or ``IngestionCancelled`` after recording the failure on the
    source; any other exception is recorded the same way and re-raised for the job's retry
    policy. A source deleted before or while its job runs ends the job quietly.
    """
    source = await session.get(Source, source_id)
    if source is None:
        log.info("ingest job %s: source %s no longer exists", job_id, source_id)
        return
    try:
        await _set_status(session, source, "extracting", job_id)
        await progress("extracting text")
        extracted = await extract(source, job_id)
        if cancelled():
            raise IngestionCancelled
        chunks = build_chunks(extracted)
        if not chunks:
            raise ExtractionError("No text could be extracted from this source.")
        if source.type == "url" and extracted.title:
            source.title = " ".join(extracted.title.split())[:TITLE_MAX_LENGTH]

        await _set_status(session, source, "embedding", job_id)
        await progress(f"embedding {len(chunks)} chunks")
        vectors = await _embed(chunks, progress, cancelled)

        source.chunk_count = await replace_chunks(session, source.id, chunks, vectors)
        await _set_status(session, source, "ready", job_id)
        log.info("source %s ready with %d chunks", source.id, source.chunk_count)
    except BaseException as exc:
        if await _record_failure(session, source_id, job_id, exc) or not isinstance(exc, Exception):
            raise
        log.info("ingest job %s: source %s was deleted while running", job_id, source_id)


def failure_message(exc: BaseException) -> str:
    if isinstance(exc, ExtractionError):
        return str(exc)
    if isinstance(exc, IngestionCancelled | asyncio.CancelledError):
        return "Ingestion was cancelled."
    return f"Ingestion failed unexpectedly ({type(exc).__name__}: {exc})."


async def _record_failure(
    session: AsyncSession, source_id: UUID, job_id: UUID, exc: BaseException
) -> bool:
    """Mark the source failed with no chunks. Returns False when the source no longer exists."""
    try:
        await session.rollback()
        source = await session.get(Source, source_id)
        if source is None:
            return False
        await session.execute(delete(Chunk).where(Chunk.source_id == source_id))
        source.status = "failed"
        source.chunk_count = 0
        source.error = failure_message(exc)
        await _publish(session, source, job_id)
    except Exception:
        log.exception("source %s: could not record the ingestion failure", source_id)
        return True
    log.warning("source %s failed: %s", source_id, source.error)
    return True
