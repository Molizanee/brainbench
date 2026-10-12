from datetime import UTC, datetime

from app.jobs.errors import PermanentJobError, ProviderLimitReached


def test_provider_limit_carries_reset_time() -> None:
    reset = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    assert ProviderLimitReached(reset).reset_at == reset
    assert ProviderLimitReached().reset_at is None
    assert str(ProviderLimitReached()) == "waiting for provider limit reset"


def test_permanent_error_has_reason() -> None:
    assert PermanentJobError("no budget", reason="budget-exhausted").reason == "budget-exhausted"
