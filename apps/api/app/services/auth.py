"""Instance password and session tokens (design D9).

The password is stored as an argon2 hash in ``settings.password_hash``. A session is an
itsdangerous-signed token carried in an ``HttpOnly`` cookie; it embeds a tag of the current hash,
so setting a new password invalidates every earlier session.
"""

import asyncio
import hashlib
import logging

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, Unauthorized
from app.db.schema import InstanceSettings
from app.models.auth import AuthState
from app.services.instance_settings import load_instance_settings

__lazy_modules__ = {"argon2", "argon2.exceptions"}
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

log = logging.getLogger(__name__)

SALT = "brainbench-session"


class PasswordAlreadySet(Conflict):
    code = "password-already-set"


class SetupRequired(Conflict):
    code = "setup-required"


class InvalidPassword(Unauthorized):
    code = "invalid-password"


def _hash_tag(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


class AuthService:
    def __init__(self, session: AsyncSession, *, max_age_s: int | None = None) -> None:
        self.session = session
        self.max_age_s = settings.session_max_age_s if max_age_s is None else max_age_s
        self.hasher = PasswordHasher()

    async def _settings(self) -> InstanceSettings:
        return await load_instance_settings(self.session)

    def _serializer(self, row: InstanceSettings) -> URLSafeTimedSerializer:
        assert row.session_secret is not None
        return URLSafeTimedSerializer(row.session_secret, salt=SALT)

    def _issue(self, row: InstanceSettings) -> str:
        assert row.password_hash is not None
        return self._serializer(row).dumps({"pw": _hash_tag(row.password_hash)})

    def _valid(self, row: InstanceSettings, token: str | None) -> bool:
        if not token or row.password_hash is None:
            return False
        try:
            data = self._serializer(row).loads(token, max_age=self.max_age_s)
        except BadSignature:
            return False
        return isinstance(data, dict) and data.get("pw") == _hash_tag(row.password_hash)

    async def state(self, token: str | None) -> AuthState:
        row = await self._settings()
        await self.session.commit()
        return AuthState(
            password_set=row.password_hash is not None, authenticated=self._valid(row, token)
        )

    async def is_authenticated(self, token: str | None) -> bool:
        return (await self.state(token)).authenticated

    async def setup(self, password: str) -> str:
        """First-run wizard: set the password once and return a session token."""
        row = await self._settings()
        if row.password_hash is not None:
            raise PasswordAlreadySet("The instance password is already set; sign in instead.")
        row.password_hash = await asyncio.to_thread(self.hasher.hash, password)
        token = self._issue(row)
        await self.session.commit()
        log.info("instance password set")
        return token

    async def login(self, password: str) -> str:
        row = await self._settings()
        if row.password_hash is None:
            raise SetupRequired("No instance password exists yet; complete the first-run setup.")
        try:
            await asyncio.to_thread(self.hasher.verify, row.password_hash, password)
        except (VerificationError, InvalidHashError) as exc:
            raise InvalidPassword("Wrong password.") from exc
        if self.hasher.check_needs_rehash(row.password_hash):
            row.password_hash = await asyncio.to_thread(self.hasher.hash, password)
        token = self._issue(row)
        await self.session.commit()
        return token
