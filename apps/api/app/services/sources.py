"""Sources: submission, listing, and deletion (material-ingestion spec, design D5).

Extraction and embedding run later in the ``ingest`` job (``app/jobs/ingest.py``).
"""

import logging
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import DomainError, NotFound
from app.db.schema import Chunk, Job, Source, Topic, TopicChunk
from app.models.sources import (
    TITLE_MAX_LENGTH,
    GitSourceCreate,
    NoteSourceCreate,
    UrlSourceCreate,
)

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
COPY_BLOCK = 1024 * 1024
ACTIVE_JOB_STATUSES = ("queued", "running")


class SourceNotFound(NotFound):
    code = "source-not-found"


class FileTooLarge(DomainError):
    code = "file-too-large"


class NotAPdf(DomainError):
    code = "not-a-pdf"


def pdf_limit_message() -> str:
    return f"PDF uploads are limited to {settings.pdf_max_bytes // (1024 * 1024)} MB."


def _clip(title: str) -> str:
    title = " ".join(title.split())
    return title if len(title) <= TITLE_MAX_LENGTH else title[: TITLE_MAX_LENGTH - 1] + "…"


def url_title(url: str) -> str:
    parts = urlsplit(url)
    return _clip(parts.netloc + parts.path.rstrip("/"))


def git_title(url: str) -> str:
    path = urlsplit(url).path.strip("/").removesuffix(".git")
    return _clip(path or urlsplit(url).netloc)


def note_title(note: NoteSourceCreate) -> str:
    if note.title:
        return _clip(note.title)
    return _clip(note.text.splitlines()[0])


def pdf_title(filename: str | None) -> str:
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    return _clip(name) if name else "Untitled PDF"


async def enqueue_ingest(session: AsyncSession, source: Source) -> Job:
    """Queue the ``ingest`` job for ``source`` and link it on ``source.job_id``.

    Thin local enqueue: section 2 ships the shared ``app.services.jobs.enqueue``; the integrator
    switches this one call to it.
    """
    job = Job(id=uuid4(), type="ingest", status="queued", payload={"source_id": str(source.id)})
    session.add(job)
    await session.flush()
    source.job_id = job.id
    return job


class SourceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _submit(self, source: Source) -> Source:
        self.session.add(source)
        await self.session.flush()
        job = await enqueue_ingest(self.session, source)
        await self.session.commit()
        await self.session.refresh(source)
        log.info("source %s (%s) queued as job %s", source.id, source.type, job.id)
        return source

    async def create(self, body: UrlSourceCreate | GitSourceCreate | NoteSourceCreate) -> Source:
        match body:
            case UrlSourceCreate(url=url):
                source = Source(id=uuid4(), type="url", uri=str(url), title=url_title(str(url)))
            case GitSourceCreate(url=url):
                source = Source(id=uuid4(), type="git", uri=str(url), title=git_title(str(url)))
            case NoteSourceCreate():
                source = Source(id=uuid4(), type="note", note=body.text, title=note_title(body))
        return await self._submit(source)

    async def create_pdf(self, filename: str | None, file: BinaryIO) -> Source:
        """Store an uploaded PDF under ``sources_dir`` and queue it.

        The router rejects oversized uploads before this point; the copy re-checks the size so
        the limit holds for any caller.
        """
        if file.read(len(PDF_MAGIC)) != PDF_MAGIC:
            raise NotAPdf("The uploaded file is not a PDF.")
        file.seek(0)

        source_id = uuid4()
        settings.sources_dir.mkdir(parents=True, exist_ok=True)
        target = settings.sources_dir / f"{source_id}.pdf"
        try:
            _copy_limited(file, target, settings.pdf_max_bytes)
            source = Source(
                id=source_id,
                type="pdf",
                title=pdf_title(filename),
                file_path=str(target.relative_to(settings.brainbench_data)),
            )
            return await self._submit(source)
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    async def list(self) -> list[Source]:
        result = await self.session.scalars(
            select(Source).order_by(Source.created_at.desc(), Source.id)
        )
        return list(result)

    async def delete(self, source_id: UUID) -> None:
        """Cancel the source's active job, orphan topics derived only from it, and remove it."""
        source = await self.session.get(Source, source_id)
        if source is None:
            raise SourceNotFound("Source not found.")

        if source.job_id is not None:
            job = await self.session.get(Job, source.job_id)
            if job is not None and job.status in ACTIVE_JOB_STATUSES:
                job.cancel_requested = True
                if job.status == "queued":
                    job.status = "cancelled"
                    job.finished_at = datetime.now(UTC)

        source_chunks = select(Chunk.id).where(Chunk.source_id == source_id)
        cited = select(TopicChunk.topic_id).where(TopicChunk.chunk_id.in_(source_chunks))
        cited_elsewhere = (
            select(TopicChunk.topic_id)
            .join(Chunk, Chunk.id == TopicChunk.chunk_id)
            .where(Chunk.source_id != source_id)
        )
        orphaned = await self.session.scalars(
            update(Topic)
            .where(
                Topic.id.in_(cited),
                Topic.id.not_in(cited_elsewhere),
                Topic.orphaned_at.is_(None),
            )
            .values(orphaned_at=datetime.now(UTC))
            .returning(Topic.id)
        )
        orphaned_ids = list(orphaned)
        await self.session.execute(delete(TopicChunk).where(TopicChunk.chunk_id.in_(source_chunks)))
        await self.session.execute(delete(Chunk).where(Chunk.source_id == source_id))
        file_path = source.file_path
        await self.session.delete(source)
        await self.session.commit()

        if file_path:
            (settings.brainbench_data / file_path).unlink(missing_ok=True)
        log.info("source %s deleted; orphaned topics: %s", source_id, orphaned_ids)


def _copy_limited(file: BinaryIO, target: Path, limit: int) -> None:
    written = 0
    with target.open("wb") as out:
        while block := file.read(COPY_BLOCK):
            written += len(block)
            if written > limit:
                raise FileTooLarge(pdf_limit_message())
            out.write(block)
