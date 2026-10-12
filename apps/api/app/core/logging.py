import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Libraries that log every HTTP request at INFO.
QUIET_LOGGERS = ("httpx", "httpcore", "huggingface_hub", "urllib3")


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging once per entrypoint."""
    logging.basicConfig(level=level, format=FORMAT, force=True)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
