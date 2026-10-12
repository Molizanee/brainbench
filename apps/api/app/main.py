from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import v1
from app.api.errors import register_exception_handlers
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.revision import ensure_schema_at_head
from app.db.session import engine
from app.services.providers import start_refresher


def create_app(*, schema_guard: bool = True, background: bool = True) -> FastAPI:
    """Build the API. ``schema_guard`` refuses to serve on a schema that is not the built head;
    ``background`` runs the provider refresher for the app's lifetime (off in tests that share
    one SQLite connection between the request and the sweep)."""
    setup_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if schema_guard:
            await ensure_schema_at_head(engine, "api")
        if not background:
            yield
            return
        async with start_refresher(app):
            yield

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
        redoc_url=None,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(v1.public_router)
    app.include_router(v1.router)
    return app


app = create_app()
