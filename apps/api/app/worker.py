"""Worker entrypoint: ``python -m app.worker``.

Start-up order: schema revision guard, host data path (self-inspection through the Docker
socket, ``BRAINBENCH_DATA_HOST`` override), sandbox probe, then the job loop. ``import app.jobs``
registers every handler. The claim loop is section 2's work; until then the worker idles.
"""

import asyncio
import logging
import sys
from pathlib import Path

import app.jobs  # noqa: F401  (registers handlers)
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.revision import SchemaMismatch, ensure_schema_at_head
from app.db.session import engine
from app.jobs.registry import HANDLERS
from app.services.host_paths import (
    HostPathError,
    SandboxProbeError,
    resolve_host_data_dir,
    run_sandbox_probe,
)

__lazy_modules__ = {"docker"}
import docker

log = logging.getLogger(__name__)


def prepare_sandboxes() -> Path:
    """Resolve the host data path and prove a sandbox can read through it."""
    client = docker.from_env()
    host_data = resolve_host_data_dir(
        client, settings.brainbench_data, settings.brainbench_data_host
    )
    run_sandbox_probe(client, settings.sandbox_probe_image, settings.brainbench_data, host_data)
    log.info("sandbox probe passed (host data dir %s)", host_data)
    return host_data


async def run() -> None:
    revision = await ensure_schema_at_head(engine, "worker")
    log.info("schema revision %s", revision)
    log.info("worker started with handlers: %s", ", ".join(sorted(HANDLERS)))
    while True:
        log.debug("no claim loop yet; sleeping %.1fs", settings.worker_idle_sleep_s)
        await asyncio.sleep(settings.worker_idle_sleep_s)


def main() -> None:
    setup_logging()
    try:
        prepare_sandboxes()
        asyncio.run(run())
    except (SchemaMismatch, HostPathError, SandboxProbeError) as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
