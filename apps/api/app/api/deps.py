from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.services.auth import AuthService


def get_auth_service(session: AsyncSession = Depends(get_session)) -> AuthService:
    return AuthService(session)


def session_token(request: Request) -> str | None:
    return request.cookies.get(settings.session_cookie_name)


async def require_session(
    token: str | None = Depends(session_token),
    auth: AuthService = Depends(get_auth_service),
) -> None:
    """Dependency on the authenticated v1 router: 401 without a valid session cookie."""
    if not await auth.is_authenticated(token):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Sign in to continue."},
        )
