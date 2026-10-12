"""``init`` service entrypoint: ``python -m app.bootstrap`` (design D11).

Runs once per ``docker compose up``, idempotently, in order:
1. ``alembic upgrade head``
2. data directories
3. vault key (only while no fingerprint is recorded; otherwise verify and leave locked)
4. Anatomy skill at the pinned commit (staged, verified, swapped)
5. embedding model pre-download

Steps 4 and 5 need the network only on first run or a pin change; their failures are logged and
do not fail bootstrap, so the rest of the instance still starts.
"""

import asyncio
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.revision import current_revision
from app.db.session import session_scope
from app.services.skill_install import (
    SkillInstallError,
    install_pinned_skill,
    installed_commit,
    read_pin,
)
from app.services.vault_key import ensure_vault_key

__lazy_modules__ = {"fastembed"}
from fastembed import TextEmbedding

log = logging.getLogger("app.bootstrap")

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


async def _revision() -> str | None:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            return await current_revision(connection)
    finally:
        await engine.dispose()


def migrate() -> None:
    before = asyncio.run(_revision())
    config = Config(str(ALEMBIC_INI))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    after = asyncio.run(_revision())
    if before == after:
        log.info("schema already at head %s; no migration ran", after)
    else:
        log.info("schema migrated from %s to %s", before or "empty", after)


def ensure_data_dirs() -> None:
    for directory in (
        settings.jobs_dir,
        settings.assets_dir,
        settings.models_dir,
        settings.skills_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    log.info("data directories ready under %s", settings.brainbench_data)


async def vault_key() -> None:
    async with session_scope() as session:
        state = await ensure_vault_key(session, settings.vault_key_path)
    if state.locked:
        log.warning(
            "vault is locked (%s): %s is missing or does not match the recorded fingerprint; "
            "no key was generated. Restore the key file from backup or reset the vault from the "
            "providers page.",
            state,
            settings.vault_key_path,
        )
    else:
        log.info("vault key %s (%s)", state, settings.vault_key_path)


def skill() -> None:
    target = settings.skills_dir / "anatomy"
    try:
        pin = read_pin(settings.anatomy_pin_file)
        result = install_pinned_skill(target, pin, settings.anatomy_repo_url)
    except (SkillInstallError, OSError) as exc:
        log.error(
            "anatomy skill not updated (installed: %s): %s", installed_commit(target) or "none", exc
        )
        return
    log.info("anatomy skill %s at %s", result, pin)


def embedding_model() -> None:
    model = settings.embedding_model
    marker = settings.models_dir / f".ready-{model.replace('/', '--')}"
    if marker.exists():
        log.info("embedding model %s already present", model)
        return
    try:
        TextEmbedding(model_name=model, cache_dir=str(settings.models_dir))
    except Exception as exc:
        log.error("embedding model %s not downloaded: %s", model, exc)
        return
    marker.touch()
    log.info("embedding model %s downloaded to %s", model, settings.models_dir)


def main() -> None:
    setup_logging()
    try:
        migrate()
        ensure_data_dirs()
        asyncio.run(vault_key())
    except Exception:
        log.exception("bootstrap failed")
        sys.exit(1)
    skill()
    embedding_model()
    log.info("bootstrap complete")


if __name__ == "__main__":
    main()
