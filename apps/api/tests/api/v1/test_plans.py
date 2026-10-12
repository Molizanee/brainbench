import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.support.curation import (
    Script,
    curate,
    install_fake_embeddings,
    install_script,
    seed_profile,
    seed_source,
)

from app.core.config import settings
from app.db.schema import StudySession


@pytest.fixture
def signed_in(client: TestClient) -> Iterator[TestClient]:
    client.post("/api/auth/setup", json={"password": "correct horse"})
    yield client


@pytest.fixture(autouse=True)
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Script:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    install_fake_embeddings(monkeypatch)
    script = Script(
        relevance={"CSS": 0.2},
        estimates={"Snapshots": 450},
        edges=[("Logs", "Terms", 0.9), ("Snapshots", "Logs", 0.8)],
    )
    install_script(monkeypatch, script)
    return script


async def seeded(session: AsyncSession) -> None:
    await seed_profile(session, budget_hours=5)
    await seed_source(
        session,
        "Raft paper",
        ["Terms: Leaders serve terms.", "Logs: Replicated logs.", "Snapshots: Compaction."],
    )
    await seed_source(session, "Web notes", ["CSS: Layout."])
    await curate(session)


def topic(plan: dict[str, Any], title: str) -> dict[str, Any]:
    return next(i for i in [*plan["items"], *plan["excluded"]] if i["title"] == title)


def edit(client: TestClient, version: int, body: dict[str, Any]) -> Any:
    return client.post(f"/api/plans/{version}/edits", json=body)


def test_requires_a_session(client: TestClient) -> None:
    assert client.get("/api/plans/current").status_code == 401


def test_no_plan_yet(signed_in: TestClient) -> None:
    response = signed_in.get("/api/plans/current")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "plan-missing"
    assert signed_in.get("/api/plans").json() == []
    assert signed_in.get("/api/plans/7").json()["detail"]["code"] == "plan-not-found"


async def test_current_plan_has_weeks_items_rationale_citations_and_metadata(
    signed_in: TestClient, session: AsyncSession
) -> None:
    await seeded(session)

    plan = signed_in.get("/api/plans/current").json()

    assert (plan["version"], plan["is_current"], plan["stale"]) == (1, True, False)
    assert plan["profile_version"] == 1 and len(plan["source_ids"]) == 2
    assert plan["run"]["threshold"] == 0.4
    assert plan["run"]["models"]["scoring"] == {"provider": "scripted", "model": "scripted-scoring"}
    assert [i["title"] for i in plan["items"]] == ["Terms", "Logs", "Snapshots"]
    assert [i["title"] for i in plan["excluded"]] == ["CSS"]
    assert plan["weekly_budget_minutes"] == 300
    assert [
        (w["week_start"], w["week_span"], w["exceeds_weekly_budget"]) for w in plan["weeks"]
    ] == [
        (1, 1, False),
        (2, 2, True),
    ]
    logs = topic(plan, "Logs")
    assert [p["title"] for p in logs["prerequisites"]] == ["Terms"]
    assert logs["serves_targets"][0]["name"] == "Distributed consensus"
    assert logs["rationale"] == "Logs serves the target."
    assert logs["status"] == "not-started" and logs["merged_into"] is None
    [citation] = logs["citations"]
    assert citation["source_title"] == "Raft paper"
    assert citation["excerpt"] == "Logs: Replicated logs."
    css = topic(plan, "CSS")
    assert css["excluded"] and css["week_start"] is None and css["relevance"] == 0.2
    assert signed_in.get("/api/plans").json()[0]["version"] == 1
    assert signed_in.get("/api/plans/1").json()["items"] == plan["items"]


async def test_stale_when_profile_or_ready_sources_change(
    signed_in: TestClient, session: AsyncSession
) -> None:
    await seeded(session)

    signed_in.patch("/api/profile", json={"weekly_budget_hours": 6})
    profile_changed = signed_in.get("/api/plans/current").json()
    await seed_source(session, "More", ["Gossip: Epidemics."])
    both = signed_in.get("/api/plans/current").json()

    assert profile_changed["stale"] is True
    assert profile_changed["stale_reasons"] == [
        "Your profile changed (version 2; this plan used version 1)."
    ]
    assert both["stale_reasons"][1] == "Your ready sources changed (1 added)."


async def test_reorder_respects_prerequisites(signed_in: TestClient, session: AsyncSession) -> None:
    await seeded(session)
    plan = signed_in.get("/api/plans/current").json()
    ids = {i["title"]: i["topic_id"] for i in plan["items"]}

    rejected = edit(
        signed_in, 1, {"op": "reorder", "order": [ids["Logs"], ids["Terms"], ids["Snapshots"]]}
    )
    incomplete = edit(signed_in, 1, {"op": "reorder", "order": [ids["Terms"]]})

    assert rejected.status_code == 400
    assert rejected.json()["detail"] == {
        "code": "prerequisite-violation",
        "message": "'Logs' must come after its prerequisite 'Terms'.",
    }
    assert incomplete.json()["detail"]["code"] == "invalid-edit"
    assert signed_in.get("/api/plans/current").json()["version"] == 1


async def test_remove_creates_a_new_version_and_keeps_the_old(
    signed_in: TestClient, session: AsyncSession
) -> None:
    await seeded(session)
    snapshots = topic(signed_in.get("/api/plans/current").json(), "Snapshots")["topic_id"]

    response = edit(signed_in, 1, {"op": "remove", "topic_id": snapshots})

    assert response.status_code == 201
    v2 = response.json()
    assert v2["version"] == 2 and v2["is_current"]
    assert [i["title"] for i in v2["items"]] == ["Terms", "Logs"]
    removed = topic(v2, "Snapshots")
    assert removed["excluded"] and removed["rationale"].startswith("Removed from the plan by you.")
    v1 = signed_in.get("/api/plans/1").json()
    assert [i["title"] for i in v1["items"]] == ["Terms", "Logs", "Snapshots"]
    assert v1["is_current"] is False and v1["current_version"] == 2
    outdated = edit(signed_in, 1, {"op": "remove", "topic_id": snapshots})
    assert outdated.status_code == 409 and outdated.json()["detail"]["code"] == "plan-outdated"


async def test_include_excluded_topic_marks_it_manually_included(
    signed_in: TestClient, session: AsyncSession
) -> None:
    await seeded(session)
    css = topic(signed_in.get("/api/plans/current").json(), "CSS")["topic_id"]

    v2 = edit(signed_in, 1, {"op": "include", "topic_id": css}).json()

    included = topic(v2, "CSS")
    assert not included["excluded"] and included["manually_included"]
    assert included["week_start"] is not None
    assert v2["excluded"] == []
    assert [i["title"] for i in v2["items"]] == ["Terms", "Logs", "Snapshots", "CSS"]
    again = edit(signed_in, 2, {"op": "include", "topic_id": css})
    assert again.status_code == 404 and again.json()["detail"]["code"] == "topic-not-in-plan"


async def test_title_edit_creates_a_revision_and_old_versions_keep_the_old_title(
    signed_in: TestClient, session: AsyncSession
) -> None:
    await seeded(session)
    logs = topic(signed_in.get("/api/plans/current").json(), "Logs")

    v2 = edit(
        signed_in, 1, {"op": "edit", "topic_id": logs["topic_id"], "title": "Log replication"}
    ).json()
    unchanged = edit(
        signed_in, 2, {"op": "edit", "topic_id": logs["topic_id"], "title": "Log replication"}
    )
    blank = edit(signed_in, 2, {"op": "edit", "topic_id": logs["topic_id"]})

    renamed = topic(v2, "Log replication")
    assert renamed["revision"] == 2 and renamed["topic_id"] == logs["topic_id"]
    assert renamed["summary"] == logs["summary"]
    assert topic(signed_in.get("/api/plans/1").json(), "Logs")["revision"] == 1
    assert unchanged.status_code == 400 and unchanged.json()["detail"]["code"] == "invalid-edit"
    assert blank.status_code == 422


async def test_merge_then_current_plan_shows_the_survivor_with_history(
    signed_in: TestClient, session: AsyncSession
) -> None:
    await seeded(session)
    plan = signed_in.get("/api/plans/current").json()
    terms, logs = topic(plan, "Terms")["topic_id"], topic(plan, "Logs")["topic_id"]
    signed_in.post(f"/api/topics/{logs}/sessions", json={"minutes": 30, "rating": "could-teach"})

    v2 = edit(signed_in, 1, {"op": "merge", "topic_id": logs, "into_topic_id": terms}).json()
    current = signed_in.get("/api/plans/current").json()
    old = signed_in.get("/api/plans/1").json()

    assert v2["version"] == current["version"] == 2
    assert [i["title"] for i in current["items"]] == ["Terms", "Snapshots"]
    survivor = topic(current, "Terms")
    assert survivor["status"] == "mastered"  # the merged topic's session moved over
    assert {c["excerpt"] for c in survivor["citations"]} == {
        "Terms: Leaders serve terms.",
        "Logs: Replicated logs.",
    }
    assert [p["title"] for p in topic(current, "Snapshots")["prerequisites"]] == ["Terms"]
    assert topic(old, "Logs")["merged_into"] == terms
    session_on_loser = signed_in.post(
        f"/api/topics/{logs}/sessions", json={"minutes": 10, "rating": "understood"}
    )
    assert session_on_loser.json()["session"]["topic_id"] == terms
    same = edit(signed_in, 2, {"op": "merge", "topic_id": terms, "into_topic_id": terms})
    assert same.status_code == 400


async def test_item_status_follows_logged_sessions(
    signed_in: TestClient, session: AsyncSession
) -> None:
    await seeded(session)
    plan = signed_in.get("/api/plans/current").json()
    snapshots = topic(plan, "Snapshots")["topic_id"]
    session.add(StudySession(topic_id=uuid.UUID(snapshots), minutes=20, rating="struggled"))
    await session.commit()

    current = signed_in.get("/api/plans/current").json()

    assert topic(current, "Snapshots")["status"] == "in-progress"
