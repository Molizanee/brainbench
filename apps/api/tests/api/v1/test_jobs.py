from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import job_queue, jobs


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    assert client.post("/api/auth/setup", json={"password": "correct horse"}).status_code == 201
    return client


async def test_jobs_require_a_session(client: TestClient) -> None:
    assert client.get("/api/jobs").status_code == 401
    assert client.post(f"/api/jobs/{uuid4()}/cancel").status_code == 401


async def test_list_and_read_job(signed_in: TestClient, session: AsyncSession) -> None:
    job = await jobs.enqueue(session, "noop", {"source_id": "s1"})
    other = await jobs.enqueue(session, "video", {})
    await session.commit()

    listed = signed_in.get("/api/jobs").json()
    assert {item["id"] for item in listed} == {str(job.id), str(other.id)}
    only_video = signed_in.get("/api/jobs", params={"type": "video"}).json()
    assert [item["id"] for item in only_video] == [str(other.id)]
    assert signed_in.get("/api/jobs", params={"status": ["running", "failed"]}).json() == []

    detail = signed_in.get(f"/api/jobs/{job.id}").json()
    assert detail["type"] == "noop"
    assert detail["status"] == "queued"
    assert detail["payload"] == {"source_id": "s1"}
    assert detail["attempt"] == 0 and detail["max_attempts"] == 3
    assert detail["progress"] is None
    assert detail["created_at"].endswith("Z") or detail["created_at"].endswith("+00:00")
    assert detail["started_at"] is None and detail["finished_at"] is None
    assert [(e["kind"], e["status"]) for e in detail["events"]] == [("status", "queued")]


async def test_read_unknown_job_is_404(signed_in: TestClient) -> None:
    response = signed_in.get(f"/api/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job-not-found"


async def test_status_filter_is_validated(signed_in: TestClient) -> None:
    assert signed_in.get("/api/jobs", params={"status": "bogus"}).status_code == 422


async def test_cancel_queued_and_running_jobs(signed_in: TestClient, session: AsyncSession) -> None:
    queued = await jobs.enqueue(session, "noop", {})
    await session.commit()
    body = signed_in.post(f"/api/jobs/{queued.id}/cancel").json()
    assert (body["status"], body["cancel_requested"]) == ("cancelled", True)

    running = await jobs.enqueue(session, "noop", {})
    await session.commit()
    await job_queue.claim_next(
        session, "w1", jobs.utcnow(), total_limit=2, type_limits={}, gates={}
    )
    await session.commit()
    body = signed_in.post(f"/api/jobs/{running.id}/cancel").json()
    assert (body["status"], body["cancel_requested"]) == ("running", True)


async def test_cancel_finished_job_is_409(signed_in: TestClient, session: AsyncSession) -> None:
    job = await jobs.enqueue(session, "noop", {})
    await session.commit()
    await job_queue.claim_next(
        session, "w1", jobs.utcnow(), total_limit=2, type_limits={}, gates={}
    )
    await job_queue.finish(session, job.id, "w1", jobs.utcnow())
    await session.commit()
    response = signed_in.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job-finished"
