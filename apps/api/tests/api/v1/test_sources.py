from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.schema import Chunk, Job, Source, Topic, TopicChunk

PDF = b"%PDF-1.7\n%fixture\n"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    return tmp_path


@pytest.fixture
def api(client: TestClient, data_dir: Path) -> Iterator[TestClient]:
    assert client.post("/api/auth/setup", json={"password": "correct horse"}).status_code == 201
    yield client


def test_requires_session(client: TestClient) -> None:
    assert client.get("/api/sources").status_code == 401
    assert client.post("/api/sources", json={"type": "note", "text": "x"}).status_code == 401


async def test_url_source_is_queued_with_ingest_job(api: TestClient, session: AsyncSession) -> None:
    response = api.post(
        "/api/sources", json={"type": "url", "url": "https://raft.github.io/raft.pdf#x"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "url"
    assert body["status"] == "queued"
    assert body["uri"] == "https://raft.github.io/raft.pdf#x"
    assert body["title"] == "raft.github.io/raft.pdf"
    assert body["chunk_count"] == 0

    job = await session.get(Job, UUID(body["job_id"]))
    assert job is not None
    assert (job.type, job.status, job.payload) == ("ingest", "queued", {"source_id": body["id"]})


@pytest.mark.parametrize("url", ["ftp://example.com/a", "not a url", "javascript:alert(1)", ""])
def test_malformed_url_rejected(api: TestClient, url: str) -> None:
    response = api.post("/api/sources", json={"type": "url", "url": url})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "url"]


def test_git_and_note_sources(api: TestClient) -> None:
    git = api.post(
        "/api/sources", json={"type": "git", "url": "https://github.com/etcd-io/raft.git"}
    ).json()
    assert (git["type"], git["title"], git["status"]) == ("git", "etcd-io/raft", "queued")

    note = api.post(
        "/api/sources", json={"type": "note", "text": "  Raft elects a leader.\nMore.  "}
    ).json()
    assert (note["type"], note["title"], note["uri"]) == ("note", "Raft elects a leader.", None)

    titled = api.post(
        "/api/sources", json={"type": "note", "title": "Notes", "text": "Body"}
    ).json()
    assert titled["title"] == "Notes"


def test_invalid_json_bodies(api: TestClient) -> None:
    empty_note = api.post("/api/sources", json={"type": "note", "text": "   "})
    assert empty_note.status_code == 422
    assert empty_note.json()["detail"][0]["loc"] == ["body", "text"]

    unknown = api.post("/api/sources", json={"type": "video", "url": "https://x.dev"})
    assert unknown.status_code == 422
    assert unknown.json()["detail"][0]["loc"] == ["body", "type"]

    garbage = api.post("/api/sources", content=b"{", headers={"content-type": "application/json"})
    assert garbage.status_code == 422


async def test_pdf_upload_is_stored_and_queued(
    api: TestClient, data_dir: Path, session: AsyncSession
) -> None:
    response = api.post("/api/sources", files={"file": ("raft paper.pdf", PDF, "application/pdf")})
    assert response.status_code == 201
    body = response.json()
    assert (body["type"], body["title"], body["status"]) == ("pdf", "raft paper.pdf", "queued")

    source = await session.get(Source, UUID(body["id"]))
    assert source is not None and source.file_path == f"sources/{body['id']}.pdf"
    assert (data_dir / source.file_path).read_bytes() == PDF


def test_oversized_pdf_rejected_with_limit(
    api: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "pdf_max_bytes", 1024)
    response = api.post(
        "/api/sources", files={"file": ("big.pdf", PDF + b"x" * 2048, "application/pdf")}
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file-too-large"
    assert api.get("/api/sources").json() == []
    assert not (data_dir / "sources").exists()


def test_default_limit_message_states_50_mb(api: TestClient) -> None:
    response = api.post(
        "/api/sources",
        files={"file": ("big.pdf", PDF, "application/pdf")},
        headers={"content-length": str(51 * 1024 * 1024)},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["message"] == "PDF uploads are limited to 50 MB."


def test_non_pdf_upload_rejected(api: TestClient) -> None:
    response = api.post("/api/sources", files={"file": ("a.pdf", b"hello", "application/pdf")})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not-a-pdf"

    missing = api.post("/api/sources", data={"type": "pdf"}, files={"other": ("a", b"x")})
    assert missing.status_code == 422
    assert missing.json()["detail"][0]["loc"] == ["body", "file"]


def test_list_shows_type_title_status_count_and_time(api: TestClient) -> None:
    api.post("/api/sources", json={"type": "note", "title": "First", "text": "a"})
    api.post("/api/sources", json={"type": "url", "url": "https://example.com/second"})
    listing = api.get("/api/sources").json()
    assert {s["title"] for s in listing} == {"First", "example.com/second"}
    for item in listing:
        assert {"type", "title", "status", "chunk_count", "created_at", "error"} <= item.keys()


async def test_delete_removes_source_chunks_file_and_orphans_sole_source_topics(
    api: TestClient, data_dir: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    pdf = api.post("/api/sources", files={"file": ("a.pdf", PDF, "application/pdf")}).json()
    other = api.post("/api/sources", json={"type": "note", "text": "other"}).json()

    async with session_factory() as session:
        source = await session.get(Source, UUID(pdf["id"]))
        assert source is not None
        mine = Chunk(source_id=source.id, position=0, text="a", location={"page": 1})
        theirs = Chunk(source_id=UUID(other["id"]), position=0, text="b", location={})
        sole, shared = Topic(), Topic()
        session.add_all([mine, theirs, sole, shared])
        await session.flush()
        session.add_all(
            [
                TopicChunk(topic_id=sole.id, chunk_id=mine.id),
                TopicChunk(topic_id=shared.id, chunk_id=mine.id),
                TopicChunk(topic_id=shared.id, chunk_id=theirs.id),
            ]
        )
        job = await session.get(Job, source.job_id)
        assert job is not None
        job.status = "running"
        await session.commit()
        sole_id, shared_id, job_id = sole.id, shared.id, job.id

    assert api.delete(f"/api/sources/{pdf['id']}").status_code == 204
    assert [s["id"] for s in api.get("/api/sources").json()] == [other["id"]]
    assert not (data_dir / "sources" / f"{pdf['id']}.pdf").exists()

    async with session_factory() as session:
        chunks = (await session.scalars(select(Chunk.source_id))).all()
        assert [str(c) for c in chunks] == [other["id"]]
        sole_topic = await session.get(Topic, sole_id)
        shared_topic = await session.get(Topic, shared_id)
        assert sole_topic is not None and sole_topic.orphaned_at is not None
        assert shared_topic is not None and shared_topic.orphaned_at is None
        running = await session.get(Job, job_id)
        assert running is not None
        assert (running.status, running.cancel_requested) == ("running", True)


async def test_delete_cancels_queued_job(
    api: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    source = api.post("/api/sources", json={"type": "note", "text": "x"}).json()
    assert api.delete(f"/api/sources/{source['id']}").status_code == 204
    async with session_factory() as session:
        job = await session.get(Job, UUID(source["job_id"]))
        assert job is not None
        assert (job.status, job.cancel_requested) == ("cancelled", True)
        assert job.finished_at is not None


def test_delete_unknown_source(api: TestClient) -> None:
    response = api.delete("/api/sources/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "source-not-found"
