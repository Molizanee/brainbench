import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging once per entrypoint."""
    logging.basicConfig(level=level, format=FORMAT, force=True)
