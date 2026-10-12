from fastapi import FastAPI


def test_app_registers_health_route(app: FastAPI) -> None:
    assert "/api/health" in app.openapi()["paths"]
