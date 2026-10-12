import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.visual_seed import SeededTopic, seed_topic, storyboard

from app.core.config import settings
from app.db.schema import Asset, InstanceSettings, Job, PlanItem, StudyPlan, Topic, TopicRevision
from app.db.schema import Storyboard as StoryboardRow
from app.services import providers
from app.services.providers import NoProvider


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


async def add_asset(
    session: AsyncSession,
    seeded: SeededTopic,
    data_dir: Path,
    *,
    format: str = "video",
    is_current: bool = True,
    previous: uuid.UUID | None = None,
) -> Asset:
    board = StoryboardRow(
        topic_id=seeded.topic_id,
        format=format,
        scenes=storyboard(seeded.chunk_ids)["scenes"],
        figure_brief={"applicable": True, "object": "ballot boxes"} if format == "figure" else None,
    )
    session.add(board)
    await session.flush()
    asset = Asset(topic_id=seeded.topic_id, format=format, storyboard_id=board.id)
    session.add(asset)
    await session.flush()
    folder = data_dir / "assets" / str(seeded.topic_id) / str(asset.id)
    folder.mkdir(parents=True)
    if format == "video":
        (folder / "lesson.mp4").write_bytes(bytes(range(256)) * 40)
        (folder / "lesson.vtt").write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nHi\n")
        files = {"video": "lesson.mp4", "captions": "lesson.vtt"}
    else:
        (folder / "figure.html").write_text("<html><svg></svg><script>1</script></html>")
        (folder / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        files = {"html": "figure.html", "png": "preview.png"}
    prefix = f"assets/{seeded.topic_id}/{asset.id}/"
    asset.files = {k: prefix + v for k, v in files.items()}
    asset.is_current = is_current
    asset.previous_asset_id = previous
    asset.script = "class Lesson(Scene): ..." if format == "video" else None
    asset.provenance = {
        "storyboard_title": "Raft leader election",
        "render_attempts": 2,
        "build_source": [{"path": "build.mjs", "size": 4, "content": "// x"}],
        "transcript_tail": "assistant: done",
    }
    await session.commit()
    return asset


async def test_topic_page_lists_assets_history_plan_and_budget(
    signed_in: TestClient, session: AsyncSession, data_dir: Path
) -> None:
    seeded = await seed_topic(session)
    old = await add_asset(session, seeded, data_dir, is_current=False)
    new = await add_asset(session, seeded, data_dir, previous=old.id)
    figure = await add_asset(session, seeded, data_dir, format="figure")
    session.add(
        TopicRevision(
            topic_id=seeded.topic_id,
            revision=2,
            title="Raft elections",
            summary="s2",
            created_by="edit",
        )
    )
    plan = StudyPlan(version=3, created_by="run", profile_version=1)
    session.add(plan)
    await session.flush()
    revision = (
        await session.execute(select(TopicRevision).where(TopicRevision.revision == 1))
    ).scalar_one()
    session.add(
        PlanItem(
            plan_id=plan.id,
            topic_id=seeded.topic_id,
            topic_revision_id=revision.id,
            position=4,
            week_start=2,
            week_span=1,
            estimate_minutes=90,
        )
    )
    await session.commit()

    response = signed_in.get(f"/api/topics/{seeded.topic_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Raft elections" and body["summary"] == "s2" and body["revision"] == 2
    assert body["status"] == "not-started"
    assert body["stats"] == {"minutes_studied": 0, "session_count": 0}
    assert [h["revision"] for h in body["history"]] == [2, 1]
    assert body["plan_item"]["plan_version"] == 3 and body["plan_item"]["position"] == 4
    assert body["budget"]["jobs_limit"] == 20 and body["budget"]["render_minutes_left"] == 600
    ids = {a["id"]: a for a in body["assets"]}
    assert set(ids) == {str(old.id), str(new.id), str(figure.id)}
    video = ids[str(new.id)]
    assert video["is_current"] and video["previous_asset_id"] == str(old.id)
    assert video["urls"] == {
        "video": f"/api/assets/{new.id}/lesson.mp4",
        "captions": f"/api/assets/{new.id}/lesson.vtt",
        "html": None,
        "preview": None,
    }
    assert video["script"].startswith("class Lesson")
    assert video["details"] == {"render_attempts": 2}
    scene = video["storyboard"]["scenes"][0]
    assert video["storyboard"]["title"] == "Raft leader election"
    assert scene["citations"][0]["source_title"] == "Raft notes"
    assert scene["citations"][0]["excerpt"].startswith("A candidate becomes leader")
    fig = ids[str(figure.id)]
    assert fig["urls"]["html"] == f"/api/assets/{figure.id}/figure.html"
    assert fig["urls"]["preview"] == f"/api/assets/{figure.id}/preview.png"
    assert fig["storyboard"]["figure_brief"]["object"] == "ballot boxes"
    assert fig["build_source"] == [{"path": "build.mjs", "size": 4, "content": "// x"}]
    assert fig["transcript_tail"] == "assistant: done"


async def test_merged_topic_redirects_to_the_survivor(
    signed_in: TestClient, session: AsyncSession
) -> None:
    survivor = await seed_topic(session)
    loser = await seed_topic(session, profile=False)
    middle = await seed_topic(session, profile=False)
    for topic_id, target in (
        (loser.topic_id, middle.topic_id),
        (middle.topic_id, survivor.topic_id),
    ):
        topic = await session.get(Topic, topic_id)
        assert topic is not None
        topic.merged_into = target
    await session.commit()
    response = signed_in.get(f"/api/topics/{loser.topic_id}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == f"/api/topics/{survivor.topic_id}"
    assert signed_in.get(f"/api/topics/{loser.topic_id}").json()["id"] == str(survivor.topic_id)
    missing = signed_in.get(f"/api/topics/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "topic-not-found"


async def test_generate_queues_dedupes_and_reports_jobs(
    signed_in: TestClient, session: AsyncSession
) -> None:
    seeded = await seed_topic(session)
    first = signed_in.post(f"/api/topics/{seeded.topic_id}/generate", json={"formats": ["video"]})
    assert first.status_code == 202, first.text
    (job,) = first.json()["jobs"]
    assert job["format"] == "video" and job["status"] == "queued" and not job["deduplicated"]
    assert first.json()["budget"]["jobs_today"] == 1
    again = signed_in.post(
        f"/api/topics/{seeded.topic_id}/generate", json={"formats": ["video", "figure"]}
    )
    jobs = {j["format"]: j for j in again.json()["jobs"]}
    assert jobs["video"]["job_id"] == job["job_id"] and jobs["video"]["deduplicated"]
    assert not jobs["figure"]["deduplicated"]
    page = signed_in.get(f"/api/topics/{seeded.topic_id}").json()
    assert {j["type"] for j in page["jobs"]} == {"video", "figure"}
    assert len((await session.execute(select(Job))).scalars().all()) == 2


async def test_generate_rejections(
    signed_in: TestClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_topic(session)
    url = f"/api/topics/{seeded.topic_id}/generate"
    assert signed_in.post(url, json={"formats": []}).status_code == 422
    assert signed_in.post(url, json={"formats": ["text"]}).status_code == 422
    row = await session.get(InstanceSettings, 1)  # created by the password setup
    assert row is not None
    row.generation_jobs_day = 0
    await session.commit()
    exhausted = signed_in.post(url, json={"formats": ["video"]})
    assert exhausted.status_code == 409
    assert exhausted.json()["detail"]["code"] == "budget-exhausted"
    assert "resets on" in exhausted.json()["detail"]["message"]

    async def none(session: AsyncSession, task_class: str, runtime: Any = None) -> None:
        raise NoProvider("No connected provider can run storyboarding.")

    monkeypatch.setattr(providers, "ensure_provider_for", none)
    no_provider = signed_in.post(url, json={"formats": ["figure"]})
    assert no_provider.status_code == 400 and no_provider.json()["detail"]["code"] == "no-provider"


def test_topic_routes_need_a_session(client: TestClient) -> None:
    topic_id = uuid.uuid4()
    assert client.get(f"/api/topics/{topic_id}").status_code == 401
    assert (
        client.post(f"/api/topics/{topic_id}/generate", json={"formats": ["video"]}).status_code
        == 401
    )
