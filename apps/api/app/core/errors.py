class DomainError(Exception):
    """Base for business-rule failures raised by services and mapped to HTTP by routers."""

    code: str = "error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFound(DomainError):
    code = "not-found"


class Conflict(DomainError):
    code = "conflict"


class Unauthorized(DomainError):
    code = "unauthorized"
