import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth import AuthService, InvalidPassword, PasswordAlreadySet, SetupRequired
from app.services.instance_settings import load_instance_settings


async def test_fresh_instance_requires_setup(session: AsyncSession) -> None:
    auth = AuthService(session)
    state = await auth.state(None)
    assert (state.password_set, state.authenticated) == (False, False)
    with pytest.raises(SetupRequired):
        await auth.login("anything")


async def test_setup_stores_argon2_hash_and_signs_in(session: AsyncSession) -> None:
    auth = AuthService(session)
    token = await auth.setup("correct horse")
    row = await load_instance_settings(session)
    assert row.password_hash is not None
    assert row.password_hash.startswith("$argon2id$")
    assert "correct horse" not in row.password_hash
    assert (await auth.state(token)).authenticated is True


async def test_setup_only_once(session: AsyncSession) -> None:
    auth = AuthService(session)
    await auth.setup("correct horse")
    with pytest.raises(PasswordAlreadySet):
        await auth.setup("another password")


async def test_login_checks_password(session: AsyncSession) -> None:
    auth = AuthService(session)
    await auth.setup("correct horse")
    with pytest.raises(InvalidPassword):
        await auth.login("wrong password")
    assert await auth.is_authenticated(await auth.login("correct horse"))


async def test_tampered_expired_or_stale_tokens_rejected(session: AsyncSession) -> None:
    auth = AuthService(session)
    token = await auth.setup("correct horse")
    assert not await auth.is_authenticated(token + "x")
    assert not await auth.is_authenticated("garbage")
    assert not await auth.is_authenticated(None)
    assert not await AuthService(session, max_age_s=-1).is_authenticated(token)

    # A new password hash invalidates sessions issued for the old one.
    row = await load_instance_settings(session)
    row.password_hash = auth.hasher.hash("rotated password")
    assert not await auth.is_authenticated(token)
