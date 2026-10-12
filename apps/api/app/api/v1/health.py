from fastapi import APIRouter

from app import __version__
from app.models.health import HealthRead

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthRead)
async def health() -> HealthRead:
    """Unauthenticated liveness probe used by compose and the web readiness log."""
    return HealthRead(status="ok", version=__version__)
