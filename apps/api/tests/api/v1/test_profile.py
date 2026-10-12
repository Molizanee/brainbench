from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

PROFILE = {
    "display_name": "Ada",
    "background": "Backend engineer, five years of Python.",
    "weekly_budget_hours": 5,
}


@pytest.fixture
def api(client: TestClient) -> Iterator[TestClient]:
    """A client signed in to the instance."""
    assert client.post("/api/auth/setup", json={"password": "correct horse"}).status_code == 201
    yield client


def create(api: TestClient, **overrides: Any) -> dict[str, Any]:
    response = api.post("/api/profile", json={**PROFILE, **overrides})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_profile_routes_need_a_session(client: TestClient) -> None:
    assert client.get("/api/profile").status_code == 401
    assert client.post("/api/profile", json=PROFILE).status_code == 401


def test_read_before_create_is_404(api: TestClient) -> None:
    response = api.get("/api/profile")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "profile-missing"


def test_create_then_read(api: TestClient) -> None:
    created = create(api)
    assert created["id"]
    assert created["display_name"] == "Ada"
    assert created["weekly_budget_hours"] == 5
    assert created["version"] == 1
    assert created["current_skills"] == []
    assert created["target_skills"] == []

    read = api.get("/api/profile")
    assert read.status_code == 200
    assert read.json() == created


def test_create_with_skills_and_targets(api: TestClient) -> None:
    created = create(
        api,
        current_skills=[
            {"name": "Python", "level": "expert"},
            {"name": "linear algebra", "level": "working"},
        ],
        target_skills=[
            {
                "name": "Linear Algebra",
                "target_level": "proficient",
                "priority": "high",
                "reason": "For ML papers",
            }
        ],
        preferences={"formats": ["figure", "video"], "depth": "rigor-first", "language": "pt-BR"},
    )
    assert [(s["name"], s["level"]) for s in created["current_skills"]] == [
        ("linear algebra", "working"),
        ("Python", "expert"),
    ]
    [target] = created["target_skills"]
    assert target["name"] == "Linear Algebra"
    assert target["reason"] == "For ML papers"
    assert target["achieved_at"] is None
    assert created["preferences"] == {
        "formats": ["video", "figure"],
        "depth": "rigor-first",
        "language": "pt-BR",
    }
    assert created["version"] == 1


def test_skill_crud_bumps_version(api: TestClient) -> None:
    create(api)

    added = api.post("/api/profile/skills", json={"name": " Rust ", "level": "novice"})
    assert added.status_code == 201
    body = added.json()
    assert body["version"] == 2
    [skill] = body["current_skills"]
    assert skill["name"] == "Rust"

    patched = api.patch(f"/api/profile/skills/{skill['id']}", json={"level": "working"})
    assert patched.status_code == 200
    assert patched.json()["current_skills"][0]["level"] == "working"
    assert patched.json()["version"] == 3

    deleted = api.delete(f"/api/profile/skills/{skill['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["current_skills"] == []
    assert deleted.json()["version"] == 4


def test_target_crud_bumps_version(api: TestClient) -> None:
    create(api)

    added = api.post(
        "/api/profile/targets",
        json={"name": "Raft", "target_level": "working", "priority": "low"},
    )
    assert added.status_code == 201
    [target] = added.json()["target_skills"]
    assert added.json()["version"] == 2

    patched = api.patch(
        f"/api/profile/targets/{target['id']}",
        json={"priority": "high", "reason": "On-call for etcd"},
    )
    assert patched.json()["target_skills"][0]["priority"] == "high"
    assert patched.json()["target_skills"][0]["reason"] == "On-call for etcd"
    assert patched.json()["version"] == 3

    cleared = api.patch(f"/api/profile/targets/{target['id']}", json={"reason": None})
    assert cleared.json()["target_skills"][0]["reason"] is None
    assert cleared.json()["version"] == 4

    deleted = api.delete(f"/api/profile/targets/{target['id']}")
    assert deleted.json()["target_skills"] == []
    assert deleted.json()["version"] == 5


def test_targets_listed_by_priority(api: TestClient) -> None:
    create(
        api,
        target_skills=[
            {"name": "b", "target_level": "working", "priority": "low"},
            {"name": "c", "target_level": "working", "priority": "high"},
            {"name": "a", "target_level": "working", "priority": "low"},
        ],
    )
    names = [t["name"] for t in api.get("/api/profile").json()["target_skills"]]
    assert names == ["c", "a", "b"]


def test_patch_profile_fields(api: TestClient) -> None:
    create(api)
    response = api.patch(
        "/api/profile",
        json={"display_name": "Ada L.", "background": "", "weekly_budget_hours": 7.5},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["display_name"], body["background"], body["weekly_budget_hours"]) == (
        "Ada L.",
        "",
        7.5,
    )
    assert body["version"] == 2


def test_patch_without_changes_keeps_version(api: TestClient) -> None:
    create(api)
    assert api.patch("/api/profile", json={}).json()["version"] == 1
    same = api.patch("/api/profile", json={"display_name": "Ada", "weekly_budget_hours": 5})
    assert same.json()["version"] == 1


def test_unknown_skill_and_target_are_404(api: TestClient) -> None:
    create(api)
    missing = "00000000-0000-0000-0000-000000000000"
    skill = api.patch(f"/api/profile/skills/{missing}", json={"level": "expert"})
    assert skill.status_code == 404
    assert skill.json()["detail"]["code"] == "skill-missing"
    target = api.delete(f"/api/profile/targets/{missing}")
    assert target.status_code == 404
    assert target.json()["detail"]["code"] == "target-missing"


def test_skill_routes_before_create_are_404(api: TestClient) -> None:
    response = api.post("/api/profile/skills", json={"name": "Go", "level": "working"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "profile-missing"
