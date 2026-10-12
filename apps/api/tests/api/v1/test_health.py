from fastapi.testclient import TestClient

from app import __version__


def test_health_is_unauthenticated_json(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok", "version": __version__}
