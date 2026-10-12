from dataclasses import asdict

from fastapi import APIRouter, Depends, status

from app.models.providers import LoginComplete, LoginRead, LoginStart, VaultRead, VaultReset
from app.services.providers import (
    ProviderRuntime,
    VaultStatus,
    get_provider_runtime,
    provider_errors,
)
from brainbench_providers.oauth.manager import LoginView

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _vault(status: VaultStatus) -> VaultRead:
    return VaultRead.model_validate(
        {"state": status.state, "detail": status.detail, "key_path": str(status.key_path)}
    )


@router.post("/vault/reset", response_model=VaultRead)
async def reset_vault(
    body: VaultReset, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> VaultRead:
    with provider_errors():
        return _vault(await runtime.reset_vault(body.confirm))


def _login(view: LoginView) -> LoginRead:
    return LoginRead.model_validate(asdict(view))


@router.post("/{provider_id}/logins", response_model=LoginRead, status_code=status.HTTP_201_CREATED)
async def start_login(
    provider_id: str,
    body: LoginStart | None = None,
    runtime: ProviderRuntime = Depends(get_provider_runtime),
) -> LoginRead:
    with provider_errors():
        return _login(await runtime.start_login(provider_id, body.kind if body else None))


@router.get("/logins/{login_id}", response_model=LoginRead)
async def get_login(
    login_id: str, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> LoginRead:
    with provider_errors():
        return _login(runtime.get_login(login_id))


@router.post("/logins/{login_id}/complete", response_model=LoginRead)
async def complete_login(
    login_id: str, body: LoginComplete, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> LoginRead:
    with provider_errors():
        return _login(await runtime.complete_login(login_id, body.input))
