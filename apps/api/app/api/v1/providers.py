from fastapi import APIRouter, Depends

from app.models.providers import VaultRead, VaultReset
from app.services.providers import (
    ProviderRuntime,
    VaultStatus,
    get_provider_runtime,
    provider_errors,
)

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
