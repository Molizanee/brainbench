from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_auth_service, session_token
from app.core.config import settings
from app.models.auth import AuthState, PasswordLogin, PasswordSetup
from app.services.auth import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_age_s,
        httponly=True,
        samesite="lax",
        secure=settings.public_url.startswith("https://"),
        path="/",
    )


@router.get("/me", response_model=AuthState)
async def me(
    token: str | None = Depends(session_token), auth: AuthService = Depends(get_auth_service)
) -> AuthState:
    return await auth.state(token)


@router.post("/setup", response_model=AuthState, status_code=status.HTTP_201_CREATED)
async def setup(
    body: PasswordSetup, response: Response, auth: AuthService = Depends(get_auth_service)
) -> AuthState:
    set_session_cookie(response, await auth.setup(body.password))
    return AuthState(password_set=True, authenticated=True)


@router.post("/login", response_model=AuthState)
async def login(
    body: PasswordLogin, response: Response, auth: AuthService = Depends(get_auth_service)
) -> AuthState:
    set_session_cookie(response, await auth.login(body.password))
    return AuthState(password_set=True, authenticated=True)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
