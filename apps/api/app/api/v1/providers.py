"""Provider connection endpoints (design D3). Paths are absolute so that the providers API and
its settings (``/api/settings/providers``) share one router."""

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.providers import (
    AccountUpdate,
    LoginComplete,
    LoginRead,
    LoginStart,
    ProviderSettingsRead,
    ProviderSettingsUpdate,
    ProvidersRead,
    VaultRead,
    VaultReset,
)
from app.services.providers import (
    ProviderRuntime,
    VaultStatus,
    get_provider_runtime,
    patch_provider_settings,
    provider_errors,
    provider_settings,
    update_provider_settings,
)
from brainbench_providers.oauth.manager import LoginView

router = APIRouter(tags=["providers"])


def _vault(status: VaultStatus) -> VaultRead:
    return VaultRead.model_validate(
        {"state": status.state, "detail": status.detail, "key_path": str(status.key_path)}
    )


def _login(view: LoginView) -> LoginRead:
    return LoginRead.model_validate(asdict(view))


@router.get("/api/providers", response_model=ProvidersRead)
async def list_providers(runtime: ProviderRuntime = Depends(get_provider_runtime)) -> ProvidersRead:
    """Registry providers with their accounts (identity, status, windows, blocks, usage today)."""
    return await runtime.overview()


@router.patch("/api/providers/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_account(
    account_id: UUID, body: AccountUpdate, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> None:
    """Set the account's priority (lower is tried first when usage is equal)."""
    await runtime.set_priority(account_id, body.priority)


@router.delete("/api/providers/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: UUID, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> None:
    """Sign out: revoke where the provider supports it and delete the stored tokens."""
    with provider_errors():
        await runtime.delete_account(account_id)


@router.post("/api/providers/vault/reset", response_model=VaultRead)
async def reset_vault(
    body: VaultReset, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> VaultRead:
    with provider_errors():
        return _vault(await runtime.reset_vault(body.confirm))


@router.post(
    "/api/providers/{provider_id}/logins",
    response_model=LoginRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_login(
    provider_id: str,
    body: LoginStart | None = None,
    runtime: ProviderRuntime = Depends(get_provider_runtime),
) -> LoginRead:
    with provider_errors():
        return _login(await runtime.start_login(provider_id, body.kind if body else None))


@router.get("/api/providers/logins/{login_id}", response_model=LoginRead)
async def get_login(
    login_id: str, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> LoginRead:
    with provider_errors():
        return _login(runtime.get_login(login_id))


@router.post("/api/providers/logins/{login_id}/complete", response_model=LoginRead)
async def complete_login(
    login_id: str, body: LoginComplete, runtime: ProviderRuntime = Depends(get_provider_runtime)
) -> LoginRead:
    with provider_errors():
        return _login(await runtime.complete_login(login_id, body.input))


@router.get("/api/settings/providers", response_model=ProviderSettingsRead)
async def get_provider_settings(
    session: AsyncSession = Depends(get_session),
    runtime: ProviderRuntime = Depends(get_provider_runtime),
) -> ProviderSettingsRead:
    """Enabled providers, explicit routing per task class, and the effective route."""
    return await provider_settings(session, runtime)


@router.put("/api/settings/providers", response_model=ProviderSettingsRead)
async def put_provider_settings(
    body: ProviderSettingsUpdate,
    session: AsyncSession = Depends(get_session),
    runtime: ProviderRuntime = Depends(get_provider_runtime),
) -> ProviderSettingsRead:
    return await update_provider_settings(session, runtime, body)


@router.patch("/api/settings/providers", response_model=ProviderSettingsRead)
async def patch_provider_settings_route(
    body: ProviderSettingsUpdate,
    session: AsyncSession = Depends(get_session),
    runtime: ProviderRuntime = Depends(get_provider_runtime),
) -> ProviderSettingsRead:
    """Partial update: only ``enabled_providers`` and/or ``routing`` present in the body."""
    return await patch_provider_settings(session, runtime, body)
