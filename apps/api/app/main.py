from fastapi import FastAPI

from app import __version__
from app.api import v1
from app.core.config import settings
from app.core.logging import setup_logging


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
        redoc_url=None,
    )
    app.include_router(v1.public_router)
    app.include_router(v1.router)
    return app


app = create_app()
