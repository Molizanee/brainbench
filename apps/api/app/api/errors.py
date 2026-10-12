from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import Conflict, DomainError, NotFound, Unauthorized

STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (NotFound, 404),
    (Conflict, 409),
    (Unauthorized, 401),
)


def status_for(error: DomainError) -> int:
    for error_type, status in STATUS_BY_ERROR:
        if isinstance(error, error_type):
            return status
    return 400


async def domain_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, DomainError)
    return JSONResponse(
        status_code=status_for(error),
        content={"detail": {"code": error.code, "message": error.message}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Domain errors become ``{"detail": {"code", "message"}}`` with a mapped status."""
    app.add_exception_handler(DomainError, domain_error_handler)
