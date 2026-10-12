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


# --- learner-profile spec scenarios -------------------------------------------------------


def test_profile_created_with_required_fields(api: TestClient) -> None:
    created = create(api)
    assert created["id"]
    assert (created["display_name"], created["background"], created["weekly_budget_hours"]) == (
        PROFILE["display_name"],
        PROFILE["background"],
        PROFILE["weekly_budget_hours"],
    )
    assert api.get("/api/profile").json()["id"] == created["id"]


def test_second_profile_rejected(api: TestClient) -> None:
    first = create(api)
    second = api.post("/api/profile", json={**PROFILE, "display_name": "Grace"})
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "profile-exists"
    assert first["id"] in detail["message"]
    assert api.get("/api/profile").json()["display_name"] == "Ada"


@pytest.mark.parametrize("budget", [0, -1, -0.5])
def test_invalid_time_budget_rejected_on_create(api: TestClient, budget: float) -> None:
    response = api.post("/api/profile", json={**PROFILE, "weekly_budget_hours": budget})
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "weekly_budget_hours"]
    assert api.get("/api/profile").status_code == 404


@pytest.mark.parametrize("budget", [0, -3])
def test_invalid_time_budget_rejected_on_update(api: TestClient, budget: float) -> None:
    create(api)
    response = api.patch("/api/profile", json={"weekly_budget_hours": budget})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "weekly_budget_hours"]
    after = api.get("/api/profile").json()
    assert (after["weekly_budget_hours"], after["version"]) == (5, 1)


def test_current_skill_added(api: TestClient) -> None:
    create(api)
    response = api.post("/api/profile/skills", json={"name": "linear algebra", "level": "working"})
    assert response.status_code == 201
    skills = api.get("/api/profile").json()["current_skills"]
    assert [(s["name"], s["level"]) for s in skills] == [("linear algebra", "working")]


@pytest.mark.parametrize("name", ["linear algebra", "Linear Algebra", "  LINEAR ALGEBRA  "])
def test_duplicate_skill_name_rejected(api: TestClient, name: str) -> None:
    create(api)
    api.post("/api/profile/skills", json={"name": "linear algebra", "level": "working"})

    response = api.post("/api/profile/skills", json={"name": name, "level": "expert"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "duplicate-skill"
    assert detail["fields"] == {"name": detail["message"]}

    after = api.get("/api/profile").json()
    assert [(s["name"], s["level"]) for s in after["current_skills"]] == [
        ("linear algebra", "working")
    ]
    assert after["version"] == 2


def test_duplicate_skill_name_rejected_on_rename(api: TestClient) -> None:
    created = create(
        api,
        current_skills=[{"name": "Go", "level": "working"}, {"name": "Rust", "level": "novice"}],
    )
    rust = next(s for s in created["current_skills"] if s["name"] == "Rust")
    response = api.patch(f"/api/profile/skills/{rust['id']}", json={"name": " go "})
    assert response.status_code == 422
    assert response.json()["detail"]["fields"].keys() == {"name"}
    names = sorted(s["name"] for s in api.get("/api/profile").json()["current_skills"])
    assert names == ["Go", "Rust"]

    # Renaming a skill to a different case of its own name is not a duplicate.
    renamed = api.patch(f"/api/profile/skills/{rust['id']}", json={"name": "rust"})
    assert renamed.status_code == 200


def test_duplicate_names_rejected_on_create(api: TestClient) -> None:
    response = api.post(
        "/api/profile",
        json={
            **PROFILE,
            "current_skills": [
                {"name": "SQL", "level": "working"},
                {"name": "sql ", "level": "expert"},
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["fields"].keys() == {"current_skills.1.name"}
    assert api.get("/api/profile").status_code == 404


def test_duplicate_target_name_rejected(api: TestClient) -> None:
    create(api)
    body = {"name": "Raft", "target_level": "working", "priority": "low"}
    api.post("/api/profile/targets", json=body)
    response = api.post("/api/profile/targets", json={**body, "name": "RAFT"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "duplicate-skill"
    assert response.json()["detail"]["fields"].keys() == {"name"}
    assert len(api.get("/api/profile").json()["target_skills"]) == 1


def test_target_skill_added(api: TestClient) -> None:
    create(api)
    response = api.post(
        "/api/profile/targets",
        json={
            "name": "distributed consensus",
            "target_level": "proficient",
            "priority": "high",
        },
    )
    assert response.status_code == 201
    [target] = api.get("/api/profile").json()["target_skills"]
    assert (target["name"], target["target_level"], target["priority"], target["reason"]) == (
        "distributed consensus",
        "proficient",
        "high",
        None,
    )


@pytest.mark.parametrize("target_level", ["working", "proficient"])
def test_target_below_current_level_rejected(api: TestClient, target_level: str) -> None:
    create(api, current_skills=[{"name": "Linear algebra", "level": "proficient"}])
    response = api.post(
        "/api/profile/targets",
        json={"name": " linear ALGEBRA", "target_level": target_level, "priority": "high"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "target-not-above-current"
    assert "must be higher than your current level" in detail["message"]
    assert detail["fields"] == {"target_level": detail["message"]}
    assert api.get("/api/profile").json()["target_skills"] == []


def test_novice_is_not_a_target_level(api: TestClient) -> None:
    create(api)
    response = api.post(
        "/api/profile/targets", json={"name": "Go", "target_level": "novice", "priority": "high"}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "target_level"]


def test_target_above_current_level_accepted(api: TestClient) -> None:
    create(api, current_skills=[{"name": "Linear algebra", "level": "working"}])
    response = api.post(
        "/api/profile/targets",
        json={"name": "linear algebra", "target_level": "proficient", "priority": "high"},
    )
    assert response.status_code == 201


def test_target_lowered_to_current_level_rejected(api: TestClient) -> None:
    created = create(
        api,
        current_skills=[{"name": "SQL", "level": "proficient"}],
        target_skills=[{"name": "SQL", "target_level": "expert", "priority": "low"}],
    )
    target_id = created["target_skills"][0]["id"]
    response = api.patch(f"/api/profile/targets/{target_id}", json={"target_level": "proficient"})
    assert response.status_code == 422
    assert response.json()["detail"]["fields"].keys() == {"target_level"}
    assert api.get("/api/profile").json()["target_skills"][0]["target_level"] == "expert"


def test_target_below_current_rejected_on_create(api: TestClient) -> None:
    response = api.post(
        "/api/profile",
        json={
            **PROFILE,
            "current_skills": [{"name": "Kafka", "level": "expert"}],
            "target_skills": [{"name": "kafka", "target_level": "expert", "priority": "low"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["fields"].keys() == {"target_skills.0.target_level"}


def test_current_skill_cannot_reach_an_active_target(api: TestClient) -> None:
    created = create(
        api,
        current_skills=[{"name": "SQL", "level": "working"}],
        target_skills=[{"name": "SQL", "target_level": "proficient", "priority": "low"}],
    )
    skill_id = created["current_skills"][0]["id"]
    response = api.patch(f"/api/profile/skills/{skill_id}", json={"level": "expert"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "target-not-above-current"
    assert response.json()["detail"]["fields"].keys() == {"level"}


def test_preferences_saved_with_defaults(api: TestClient) -> None:
    created = create(api)
    assert created["preferences"] == {
        "formats": ["video", "figure", "text"],
        "depth": "intuition-first",
        "language": "en",
    }


def test_preferences_updated(api: TestClient) -> None:
    create(api)
    response = api.patch(
        "/api/profile", json={"preferences": {"formats": ["video"], "depth": "rigor-first"}}
    )
    assert response.status_code == 200
    preferences = api.get("/api/profile").json()["preferences"]
    assert preferences == {"formats": ["video"], "depth": "rigor-first", "language": "en"}


@pytest.mark.parametrize(
    ("preferences", "loc"),
    [
        ({"formats": []}, ["body", "preferences", "formats"]),
        ({"formats": ["audio"]}, ["body", "preferences", "formats", 0]),
        ({"depth": "deep"}, ["body", "preferences", "depth"]),
        ({"language": "not a language"}, ["body", "preferences", "language"]),
    ],
)
def test_invalid_preferences_rejected(
    api: TestClient, preferences: dict[str, Any], loc: list[Any]
) -> None:
    create(api)
    response = api.patch("/api/profile", json={"preferences": preferences})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == loc


def test_version_increments_on_update(api: TestClient) -> None:
    create(api)
    api.post("/api/profile/skills", json={"name": "Go", "level": "working"})
    api.patch("/api/profile", json={"weekly_budget_hours": 8})
    assert api.get("/api/profile").json()["version"] == 3

    response = api.post(
        "/api/profile/targets",
        json={"name": "distributed consensus", "target_level": "proficient", "priority": "high"},
    )
    assert response.json()["version"] == 4


def test_rejected_change_keeps_version(api: TestClient) -> None:
    create(api, current_skills=[{"name": "Go", "level": "working"}])
    api.post("/api/profile/skills", json={"name": "go", "level": "expert"})
    api.patch("/api/profile", json={"weekly_budget_hours": 0})
    assert api.get("/api/profile").json()["version"] == 1


def test_read_returns_current_version(api: TestClient) -> None:
    create(api)
    api.patch("/api/profile", json={"preferences": {"language": "pt-BR"}})
    body = api.get("/api/profile").json()
    assert body["version"] == 2
