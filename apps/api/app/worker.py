"""Worker entrypoint: ``python -m app.worker``.

Loads every job handler (``import app.jobs`` registers them). The claim loop is section 2's
work; until then the worker logs the registered handlers and idles.
"""

import asyncio
import logging

import app.jobs  # noqa: F401  (registers handlers)
from app.core.config import settings
from app.core.logging import setup_logging
from app.jobs.registry import HANDLERS

log = logging.getLogger(__name__)


async def run() -> None:
    log.info("worker started with handlers: %s", ", ".join(sorted(HANDLERS)))
    while True:
        log.debug("no claim loop yet; sleeping %.1fs", settings.worker_idle_sleep_s)
        await asyncio.sleep(settings.worker_idle_sleep_s)


def main() -> None:
    setup_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
