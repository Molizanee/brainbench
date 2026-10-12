from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.support.curation import seed_profile, seed_source

from app.services import providers
from app.services.providers import ProviderRuntime
from brainbench_providers.testing import FakeProviders


@pytest.fixture
def signed_in(client: TestClient) -> Iterator[TestClient]:
    client.post("/api/auth/setup", json={"password": "correct horse"})
    yield client


@pytest.fixture
def providers_ok(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    checked: list[str] = []

    async def ensure(session: AsyncSession, task_class: str) -> None:
        checked.append(task_class)

    monkeypatch.setattr(providers, "ensure_provider_for", ensure)
    return checked


def test_requires_a_session(client: TestClient) -> None:
    assert client.post("/api/curation/runs").status_code == 401
    assert client.get("/api/curation/runs").status_code == 401


async def test_rejected_without_targets(
    signed_in: TestClient, session: AsyncSession, providers_ok: list[str]
) -> None:
    response = signed_in.post("/api/curation/runs")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "targets-required"

    await seed_profile(session, targets=[])
    response = signed_in.post("/api/curation/runs")
    assert response.json()["detail"]["code"] == "targets-required"
    assert "target skill" in response.json()["detail"]["message"]


async def test_rejected_without_ready_sources(
    signed_in: TestClient, session: AsyncSession, providers_ok: list[str]
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft", ["Raft: a"], status="extracting")

    response = signed_in.post("/api/curation/runs")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ready-sources-required"


async def test_rejected_without_a_connected_provider(
    signed_in: TestClient,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ProviderRuntime(
        providers.get_registry(),
        session_factory,
        tmp_path / "vault.key",
        http=FakeProviders().http_factory(),
    )
    monkeypatch.setattr(providers, "get_provider_runtime", lambda: runtime)
    await seed_profile(session)
    await seed_source(session, "Raft", ["Raft: a"])

    response = signed_in.post("/api/curation/runs")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "no-provider"


async def test_run_started_and_deduplicated(
    signed_in: TestClient, session: AsyncSession, providers_ok: list[str]
) -> None:
    await seed_profile(session)
    source, _ = await seed_source(session, "Raft", ["Raft: a"])

    first = signed_in.post("/api/curation/runs", json={"threshold": 0.5})
    second = signed_in.post("/api/curation/runs")

    assert first.status_code == 202
    body = first.json()
    assert body["status"] == "queued" and body["job_id"]
    assert (body["threshold"], body["profile_version"]) == (0.5, 1)
    assert body["source_ids"] == [str(source.id)]
    assert body["plan_version"] is None and body["step_log"] == []
    assert second.json()["id"] == body["id"]
    assert set(providers_ok) == {"topic_extraction", "topic_merge", "scoring", "prerequisites"}
    listed = signed_in.get("/api/curation/runs").json()
    assert [r["id"] for r in listed] == [body["id"]]
    job = signed_in.get(f"/api/jobs/{body['job_id']}").json()
    assert job["type"] == "curation" and job["payload"] == {"run_id": body["id"]}


def test_threshold_is_validated(signed_in: TestClient) -> None:
    response = signed_in.post("/api/curation/runs", json={"threshold": 1.5})
    assert response.status_code == 422
