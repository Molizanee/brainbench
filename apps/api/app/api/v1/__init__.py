"""API v1 routers.

``public_router`` carries the unauthenticated endpoints. ``router`` is the authenticated API:
each section adds its module under ``app/api/v1/`` (a router that declares its own full path
prefix, e.g. ``APIRouter(prefix="/api/profile")`` or ``/events``) and registers it below with a
single ``router.include_router(...)`` line. ``app/main.py`` includes both routers once.
"""

from fastapi import APIRouter

from app.api.v1 import health

public_router = APIRouter()
public_router.include_router(health.router)

router = APIRouter()
