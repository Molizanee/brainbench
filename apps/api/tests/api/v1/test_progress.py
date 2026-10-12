import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import (
    CurrentSkill,
    PlanItem,
    Profile,
    StudyPlan,
    TargetSkill,
    Topic,
    TopicRevision,
)


@pytest.fixture
def signed_in(client: TestClient) -> Iterator[TestClient]:
    client.post("/api/auth/setup", json={"password": "correct horse"})
    yield client


async def seed(session: AsyncSession) -> tuple[TargetSkill, list[Topic]]:
    """A profile (5 h/week), one target, and a plan with two topics serving it."""
    profile = Profile(display_name="Ada", weekly_budget_hours=5)
    session.add(profile)
    await session.flush()
    target = TargetSkill(
        profile_id=profile.id,
        name="Distributed consensus",
        name_normalized="distributed consensus",
        target_level="proficient",
        priority="high",
    )
    plan = StudyPlan(version=1, created_by="run", profile_version=1)
    topics = [Topic(), Topic()]
    session.add_all([target, plan, *topics])
    await session.flush()
    for position, (topic, title) in enumerate(zip(topics, ["Raft", "Paxos"], strict=True)):
        revision = TopicRevision(
            topic_id=topic.id, revision=1, title=title, summary="...", created_by="run"
        )
        session.add(revision)
        await session.flush()
        session.add(
            PlanItem(
                plan_id=plan.id,
                topic_id=topic.id,
                topic_revision_id=revision.id,
                position=position,
                week_start=1,
                week_span=1,
                serves_targets=[str(target.id)],
                estimate_minutes=120,
            )
        )
    await session.commit()
    return target, topics


def test_endpoints_require_a_session(client: TestClient) -> None:
    client.post("/api/auth/setup", json={"password": "correct horse"})
    client.cookies.clear()
    topic = uuid.uuid4()
    assert client.get("/api/progress").status_code == 401
    body = {"minutes": 30, "rating": "understood"}
    assert client.post(f"/api/topics/{topic}/sessions", json=body).status_code == 401


async def test_log_session_updates_topic(signed_in: TestClient, session: AsyncSession) -> None:
    _, topics = await seed(session)
    response = signed_in.post(
        f"/api/topics/{topics[0].id}/sessions",
        json={"minutes": 45, "rating": "understood", "note": "  log replication  "},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session"]["minutes"] == 45
    assert body["session"]["note"] == "log replication"
    assert body["topic"] == {
        "topic_id": str(topics[0].id),
        "status": "in-progress",
        "minutes_studied": 45,
        "session_count": 1,
    }
    assert body["promoted_targets"] == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"minutes": 0, "rating": "understood"}, "minutes"),
        ({"minutes": -5, "rating": "understood"}, "minutes"),
        ({"minutes": 30, "rating": "mastered"}, "rating"),
        ({"minutes": 30}, "rating"),
    ],
)
async def test_invalid_session_rejected(
    signed_in: TestClient, session: AsyncSession, body: dict[str, object], field: str
) -> None:
    _, topics = await seed(session)
    response = signed_in.post(f"/api/topics/{topics[0].id}/sessions", json=body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]


def test_unknown_topic_is_404(signed_in: TestClient) -> None:
    response = signed_in.post(
        f"/api/topics/{uuid.uuid4()}/sessions", json={"minutes": 30, "rating": "understood"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "topic-not-found"


async def test_session_on_merged_topic_lands_on_survivor(
    signed_in: TestClient, session: AsyncSession
) -> None:
    _, (survivor, _) = await seed(session)
    loser = Topic(merged_into=survivor.id)
    session.add(loser)
    await session.commit()
    response = signed_in.post(
        f"/api/topics/{loser.id}/sessions", json={"minutes": 20, "rating": "confident"}
    )
    assert response.status_code == 201
    assert response.json()["topic"]["topic_id"] == str(survivor.id)


def test_progress_without_profile_or_plan(signed_in: TestClient) -> None:
    body = signed_in.get("/api/progress").json()
    assert body["targets"] == []
    assert body["plan_version"] is None
    assert body["profile_version"] is None
    assert body["minutes_studied"] == 0
    assert body["week"]["budget_minutes"] is None
    assert body["week"]["used_pct"] is None


async def test_progress_summary_per_target(signed_in: TestClient, session: AsyncSession) -> None:
    target, (raft, paxos) = await seed(session)
    signed_in.post(f"/api/topics/{raft.id}/sessions", json={"minutes": 90, "rating": "confident"})
    signed_in.post(f"/api/topics/{paxos.id}/sessions", json={"minutes": 30, "rating": "struggled"})

    body = signed_in.get("/api/progress").json()
    assert body["plan_version"] == 1
    assert body["minutes_studied"] == 120
    assert body["week"]["minutes_studied"] == 120
    assert body["week"]["budget_minutes"] == 300
    assert body["week"]["used_pct"] == 40.0
    [summary] = body["targets"]
    assert summary["id"] == str(target.id)
    assert summary["counts"] == {"mastered": 1, "in_progress": 1, "not_started": 0}
    assert summary["minutes_studied"] == 120
    assert summary["minutes_remaining"] == 120
    assert [(t["title"], t["status"]) for t in summary["topics"]] == [
        ("Raft", "mastered"),
        ("Paxos", "in-progress"),
    ]


async def test_last_mastered_topic_promotes_the_target(
    signed_in: TestClient, session: AsyncSession
) -> None:
    target, (raft, paxos) = await seed(session)
    first = signed_in.post(
        f"/api/topics/{raft.id}/sessions", json={"minutes": 60, "rating": "confident"}
    )
    assert first.json()["promoted_targets"] == []

    last = signed_in.post(
        f"/api/topics/{paxos.id}/sessions", json={"minutes": 60, "rating": "could-teach"}
    )
    assert last.json()["promoted_targets"] == [
        {"id": str(target.id), "name": "Distributed consensus", "level": "proficient"}
    ]
    session.expire_all()
    skills = (await session.scalars(select(CurrentSkill))).all()
    assert [(s.name, s.level) for s in skills] == [("Distributed consensus", "proficient")]

    body = signed_in.get("/api/progress").json()
    assert body["profile_version"] == 2
    assert body["targets"][0]["achieved_at"] is not None
    assert body["targets"][0]["minutes_remaining"] == 0


def test_utc_offset_is_bounded(signed_in: TestClient) -> None:
    assert signed_in.get("/api/progress?utc_offset_minutes=900").status_code == 422
    assert signed_in.get("/api/progress?utc_offset_minutes=-180").status_code == 200
