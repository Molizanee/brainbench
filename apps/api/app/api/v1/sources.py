from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.db.session import get_session
from app.models.sources import (
    GitSourceCreate,
    NoteSourceCreate,
    SourceRead,
    UrlSourceCreate,
    source_create_adapter,
)
from app.services.sources import SourceService, pdf_limit_message

router = APIRouter(prefix="/api/sources", tags=["sources"])

# Room for the multipart envelope around a file that is exactly at the limit.
MULTIPART_OVERHEAD = 64 * 1024

CREATE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        UrlSourceCreate.model_json_schema(),
                        GitSourceCreate.model_json_schema(),
                        NoteSourceCreate.model_json_schema(),
                    ]
                }
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "type": {"const": "pdf"},
                        "file": {"type": "string", "format": "binary"},
                    },
                }
            },
        },
    }
}


def get_source_service(session: AsyncSession = Depends(get_session)) -> SourceService:
    return SourceService(session)


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail={"code": "file-too-large", "message": pdf_limit_message()},
    )


def _validation_error(loc: tuple[str | int, ...], message: str) -> RequestValidationError:
    return RequestValidationError([{"type": "value_error", "loc": loc, "msg": message}])


async def _json_body(request: Request) -> UrlSourceCreate | GitSourceCreate | NoteSourceCreate:
    try:
        payload = await request.json()
    except ValueError:
        raise _validation_error(("body",), "Body must be JSON or multipart/form-data.") from None
    try:
        return source_create_adapter.validate_python(payload)
    except ValidationError as exc:
        errors = []
        for error in exc.errors(include_url=False, include_context=False):
            # Drop the discriminator tag from field locations so `url` stays `url`.
            loc = ("type",) if error["type"].startswith("union_tag") else error["loc"][1:]
            errors.append({**error, "loc": ("body", *loc)})
        raise RequestValidationError(errors, body=payload) from None


@router.post(
    "",
    response_model=SourceRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=CREATE_BODY,
)
async def create_source(
    request: Request, sources: SourceService = Depends(get_source_service)
) -> SourceRead:
    """JSON for URL, Git and note sources; ``multipart/form-data`` with ``file`` for a PDF."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return SourceRead.model_validate(await sources.create(await _json_body(request)))

    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.pdf_max_bytes + MULTIPART_OVERHEAD:
        raise _too_large()
    async with request.form(max_files=1, max_fields=4) as form:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise _validation_error(("body", "file"), "Attach a PDF file.")
        if form.get("type", "pdf") != "pdf":
            raise _validation_error(("body", "type"), "Multipart uploads must be PDFs.")
        if file.size is not None and file.size > settings.pdf_max_bytes:
            raise _too_large()
        source = await sources.create_pdf(file.filename, file.file)
    return SourceRead.model_validate(source)


@router.get("", response_model=list[SourceRead])
async def list_sources(sources: SourceService = Depends(get_source_service)) -> list[SourceRead]:
    return [SourceRead.model_validate(source) for source in await sources.list()]


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: UUID, sources: SourceService = Depends(get_source_service)
) -> None:
    await sources.delete(source_id)
