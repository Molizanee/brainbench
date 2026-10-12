from datetime import datetime


class PermanentJobError(Exception):
    """A failure that must not be retried (validation error, budget exhausted, cancelled).

    ``reason`` is the machine-readable failure reason stored on the job (for example
    ``budget-exhausted`` or ``timeout``).
    """

    def __init__(self, message: str, *, reason: str = "permanent") -> None:
        super().__init__(message)
        self.reason = reason


class JobCancelled(PermanentJobError):
    """Raised by ``JobContext.raise_if_cancelled`` when a handler sees the cancel flag.

    The worker records ``cancelled`` when a cancel was requested and requeues the job when the
    worker itself is stopping; partial outputs are discarded either way.
    """

    def __init__(self, message: str = "cancelled") -> None:
        super().__init__(message, reason="cancelled")


class ProviderLimitReached(Exception):
    """The provider reported its subscription usage limit; the job is deferred, not failed.

    The worker saves the handler checkpoint, re-queues the job with
    ``run_after = reset_at + jitter`` (or now + 5 min when ``reset_at`` is None), and leaves the
    attempt count unchanged.
    """

    def __init__(self, reset_at: datetime | None = None, message: str | None = None) -> None:
        super().__init__(message or "waiting for provider limit reset")
        self.reset_at = reset_at


class JobSkipped(Exception):
    """The job has nothing to do (for example a figure that does not apply to the topic).

    The worker records status ``skipped`` with ``message`` as the progress text and commits the
    handler's session, so anything the handler stored before deciding (a storyboard) is kept.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
