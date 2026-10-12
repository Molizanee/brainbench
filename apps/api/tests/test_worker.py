import app.worker
from app.jobs.registry import HANDLERS


def test_worker_import_registers_handlers() -> None:
    assert callable(app.worker.main)
    assert "noop" in HANDLERS
