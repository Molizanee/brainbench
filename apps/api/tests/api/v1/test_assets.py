import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.visual_seed import seed_topic

from app.api.v1.assets import FIGURE_CSP
from app.core.config import settings
from app.db.schema import Asset, Job, PlanItem, StudyPlan, TopicRevision
from app.services import providers


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "brainbench_data", data)
    return data


@pytest.fixture(autouse=True)
def provider_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ensure(session: AsyncSession, task_class: str, runtime: Any = None) -> None:
        return None

    monkeypatch.setattr(providers, "ensure_provider_for", ensure)


@pytest.fixture
def signed_in(client: TestClient) -> Iterator[TestClient]:
    client.post("/api/auth/setup", json={"password": "correct horse"})
    yield client


VIDEO = bytes(range(256)) * 40


async def stored_asset(session: AsyncSession, data_dir: Path, format: str) -> Asset:
    seeded = await seed_topic(session)
    asset = Asset(topic_id=seeded.topic_id, format=format, provenance={})
    session.add(asset)
    await session.flush()
    folder = data_dir / "assets" / str(seeded.topic_id) / str(asset.id)
    folder.mkdir(parents=True)
    prefix = f"assets/{seeded.topic_id}/{asset.id}"
    if format == "video":
        (folder / "lesson.mp4").write_bytes(VIDEO)
        (folder / "lesson.vtt").write_text("WEBVTT\n")
        asset.files = {"video": f"{prefix}/lesson.mp4", "captions": f"{prefix}/lesson.vtt"}
    else:
        (folder / "figure.html").write_text("<html><svg></svg></html>")
        (folder / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        asset.files = {"html": f"{prefix}/figure.html", "png": f"{prefix}/preview.png"}
    await session.commit()
    return asset


async def test_video_is_served_with_range_support(
    signed_in: TestClient, session: AsyncSession, data_dir: Path
) -> None:
    asset = await stored_asset(session, data_dir, "video")
    full = signed_in.get(f"/api/assets/{asset.id}/lesson.mp4")
    assert full.status_code == 200 and full.content == VIDEO
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes"
    part = signed_in.get(f"/api/assets/{asset.id}/lesson.mp4", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == VIDEO[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(VIDEO)}"
    tail = signed_in.get(f"/api/assets/{asset.id}/lesson.mp4", headers={"Range": "bytes=-10"})
    assert tail.status_code == 206 and tail.content == VIDEO[-10:]
    captions = signed_in.get(f"/api/assets/{asset.id}/lesson.vtt")
    assert captions.headers["content-type"].startswith("text/vtt") and captions.text == "WEBVTT\n"
    assert "content-security-policy" not in captions.headers


async def test_figure_html_carries_the_sandboxing_csp(
    signed_in: TestClient, session: AsyncSession, data_dir: Path
) -> None:
    asset = await stored_asset(session, data_dir, "figure")
    response = signed_in.get(f"/api/assets/{asset.id}/figure.html")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src data:"
    )
    assert response.headers["content-security-policy"] == FIGURE_CSP
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    png = signed_in.get(f"/api/assets/{asset.id}/preview.png")
    assert png.headers["content-type"] == "image/png"


async def test_missing_files_and_names(
    signed_in: TestClient, session: AsyncSession, data_dir: Path
) -> None:
    asset = await stored_asset(session, data_dir, "figure")
    no_video = signed_in.get(f"/api/assets/{asset.id}/lesson.mp4")
    assert no_video.status_code == 404
    assert no_video.json()["detail"]["code"] == "asset-file-not-found"
    assert signed_in.get(f"/api/assets/{asset.id}/../../vault.key").status_code == 404
    assert signed_in.get(f"/api/assets/{asset.id}/secret.txt").status_code == 422
    unknown = signed_in.get(f"/api/assets/{uuid.uuid4()}/figure.html")
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "asset-not-found"
    # a stored path that escapes assets/ is never served
    asset.files = {**asset.files, "html": "vault.key"}
    (data_dir / "vault.key").write_text("secret")
    await session.commit()
    assert signed_in.get(f"/api/assets/{asset.id}/figure.html").status_code == 404


async def test_regenerate_with_feedback(
    signed_in: TestClient, session: AsyncSession, data_dir: Path
) -> None:
    asset = await stored_asset(session, data_dir, "video")
    url = f"/api/assets/{asset.id}/regenerate"
    assert signed_in.post(url, json={"feedback": "   "}).status_code == 422
    response = signed_in.post(url, json={"feedback": "too fast, show the proof step by step"})
    assert response.status_code == 202, response.text
    (job,) = response.json()["jobs"]
    stored = await session.get(Job, uuid.UUID(job["job_id"]))
    assert stored is not None and stored.payload["previous_asset_id"] == str(asset.id)
    assert stored.payload["feedback"] == "too fast, show the proof step by step"
    missing = signed_in.post(f"/api/assets/{uuid.uuid4()}/regenerate", json={"feedback": "x"})
    assert missing.status_code == 404


async def test_plan_generation_dry_run_then_confirm(
    signed_in: TestClient, session: AsyncSession
) -> None:
    assert signed_in.post("/api/plans/current/generate?dry_run=true").status_code == 404
    seeded = await seed_topic(session)
    plan = StudyPlan(version=1, created_by="run", profile_version=1)
    session.add(plan)
    await session.flush()
    revision = (await session.execute(select(TopicRevision))).scalar_one()
    session.add(
        PlanItem(
            plan_id=plan.id, topic_id=seeded.topic_id, topic_revision_id=revision.id, position=0
        )
    )
    await session.commit()

    preview = signed_in.post("/api/plans/current/generate?dry_run=true")
    assert preview.status_code == 200
    body = preview.json()
    assert body["dry_run"] and body["job_count"] == 2 and body["formats"] == ["video", "figure"]
    assert [(j["title"], j["format"], j["job_id"]) for j in body["jobs"]] == [
        ("Raft leader election", "video", None),
        ("Raft leader election", "figure", None),
    ]
    count = select(func.count()).select_from(Job)
    assert (await session.execute(count)).scalar_one() == 0

    confirmed = signed_in.post("/api/plans/current/generate")
    assert confirmed.json()["job_count"] == 2 and not confirmed.json()["dry_run"]
    assert all(j["job_id"] for j in confirmed.json()["jobs"])
    assert confirmed.json()["budget"]["jobs_today"] == 2
