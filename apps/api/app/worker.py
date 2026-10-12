"""Worker entrypoint: ``python -m app.worker``.

Start-up order: schema revision guard, host data path (self-inspection through the Docker
socket, ``BRAINBENCH_DATA_HOST`` override), sandbox probe, then the job loop. ``app.jobs.handlers``
registers every handler. SIGTERM and SIGINT stop claiming, kill running sandboxes, and hand the
running jobs back to the queue before the process exits.
"""

import asyncio
import logging
import os
import signal
import socket
import sys
from pathlib import Path

import app.jobs.handlers  # noqa: F401  (registers handlers)
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.revision import SchemaMismatch, ensure_schema_at_head
from app.db.session import SessionFactory, engine
from app.jobs.registry import HANDLERS
from app.jobs.runner import Runner
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
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    runner = Runner(SessionFactory, worker_id=f"{socket.gethostname()}:{os.getpid()}")
    await runner.run(stop)
    log.info("worker stopped")


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
