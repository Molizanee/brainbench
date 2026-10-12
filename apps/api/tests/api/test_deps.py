from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import v1
from app.api.deps import require_session
from app.db.session import get_session
from app.main import create_app


def test_v1_router_requires_session() -> None:
    dependencies = [d.dependency for d in v1.router.dependencies]
    assert require_session in dependencies


def make_app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = create_app(schema_guard=False)
    protected = APIRouter(dependencies=[Depends(require_session)])

    @protected.get("/api/secret")
    async def secret() -> dict[str, str]:
        return {"secret": "ok"}

    async def override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.include_router(protected)
    app.dependency_overrides[get_session] = override
    return app


def test_protected_route_401_until_signed_in(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with TestClient(make_app(session_factory)) as client:
        denied = client.get("/api/secret")
        assert denied.status_code == 401
        assert denied.json()["detail"]["code"] == "unauthenticated"

        client.post("/api/auth/setup", json={"password": "correct horse"})
        assert client.get("/api/secret").json() == {"secret": "ok"}
