from fastapi.testclient import TestClient

from app.core.config import settings

COOKIE = settings.session_cookie_name


def test_first_run_wizard_flow(client: TestClient) -> None:
    assert client.get("/api/auth/me").json() == {"password_set": False, "authenticated": False}

    response = client.post("/api/auth/setup", json={"password": "correct horse"})
    assert response.status_code == 201
    set_cookie = response.headers["set-cookie"]
    assert f"{COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert client.get("/api/auth/me").json() == {"password_set": True, "authenticated": True}


def test_setup_validation_and_conflict(client: TestClient) -> None:
    short = client.post("/api/auth/setup", json={"password": "short"})
    assert short.status_code == 422
    assert short.json()["detail"][0]["loc"] == ["body", "password"]

    client.post("/api/auth/setup", json={"password": "correct horse"})
    again = client.post("/api/auth/setup", json={"password": "another password"})
    assert again.status_code == 409
    assert again.json() == {
        "detail": {
            "code": "password-already-set",
            "message": "The instance password is already set; sign in instead.",
        }
    }


def test_logout_then_login(client: TestClient) -> None:
    client.post("/api/auth/setup", json={"password": "correct horse"})

    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.clear()
    assert client.get("/api/auth/me").json()["authenticated"] is False

    wrong = client.post("/api/auth/login", json={"password": "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid-password"

    right = client.post("/api/auth/login", json={"password": "correct horse"})
    assert right.status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is True


def test_login_before_setup_is_rejected(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "anything"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "setup-required"


def test_health_needs_no_session(client: TestClient) -> None:
    client.post("/api/auth/setup", json={"password": "correct horse"})
    client.cookies.clear()
    assert client.get("/api/health").status_code == 200
