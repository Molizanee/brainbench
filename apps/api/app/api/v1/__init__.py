"""API v1 routers.

``public_router`` carries the unauthenticated endpoints (health, auth). ``router`` is the
authenticated API (401 without a valid session cookie): each section adds its module under
``app/api/v1/`` (a router that declares its own full path prefix, e.g.
``APIRouter(prefix="/api/profile")`` or a bare ``/events`` route) and registers it below with a
single ``router.include_router(...)`` line. ``app/main.py`` includes both routers once.
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_session
from app.api.v1 import auth, events, health, jobs, profile, providers, search, sources

public_router = APIRouter()
public_router.include_router(health.router)
public_router.include_router(auth.router)

router = APIRouter(dependencies=[Depends(require_session)])
router.include_router(jobs.router)
router.include_router(events.router)
router.include_router(providers.router)
router.include_router(profile.router)
router.include_router(sources.router)
router.include_router(search.router)
