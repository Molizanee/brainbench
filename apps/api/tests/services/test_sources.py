from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.schema import Job, Source
from app.models.sources import NoteSourceCreate
from app.services.sources import (
    FileTooLarge,
    NotAPdf,
    SourceService,
    enqueue_ingest,
    git_title,
    pdf_title,
    url_title,
)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    return tmp_path


async def test_enqueue_ingest_inserts_queued_job_and_links_it(session: AsyncSession) -> None:
    source = Source(type="note", note="x", title="x")
    session.add(source)
    await session.flush()
    job = await enqueue_ingest(session, source)
    await session.commit()

    assert source.job_id == job.id
    stored = await session.get(Job, job.id)
    assert stored is not None
    assert (stored.type, stored.status, stored.payload) == (
        "ingest",
        "queued",
        {"source_id": str(source.id)},
    )


async def test_pdf_copy_enforces_limit_without_declared_size(
    session: AsyncSession, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "pdf_max_bytes", 10)
    with pytest.raises(FileTooLarge, match="limited to"):
        await SourceService(session).create_pdf("a.pdf", BytesIO(b"%PDF-" + b"x" * 20))
    assert list((data_dir / "sources").iterdir()) == []
    assert await session.scalar(select(func.count()).select_from(Source)) == 0


async def test_pdf_magic_checked(session: AsyncSession) -> None:
    with pytest.raises(NotAPdf):
        await SourceService(session).create_pdf("a.pdf", BytesIO(b"<html>"))


async def test_note_title_from_first_line(session: AsyncSession) -> None:
    source = await SourceService(session).create(
        NoteSourceCreate(type="note", text="Line one\nline two")
    )
    assert source.title == "Line one"
    assert (source.note, source.status) == ("Line one\nline two", "queued")


def test_titles() -> None:
    assert url_title("https://example.com/docs/raft/") == "example.com/docs/raft"
    assert git_title("https://github.com/etcd-io/raft.git") == "etcd-io/raft"
    assert git_title("https://example.com") == "example.com"
    assert pdf_title("C:\\papers\\raft.pdf") == "raft.pdf"
    assert pdf_title(None) == "Untitled PDF"
    assert len(url_title("https://example.com/" + "a" * 500)) == 200
