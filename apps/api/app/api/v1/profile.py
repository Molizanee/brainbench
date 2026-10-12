import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.profile import (
    CurrentSkillCreate,
    CurrentSkillUpdate,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    TargetSkillCreate,
    TargetSkillUpdate,
)
from app.services.profile import ProfileService, ProfileValidationError


def field_errors() -> Iterator[None]:
    """Rules checked against the stored profile answer 422 with per-field messages.

    Body: ``{"detail": {"code", "message", "fields": {"<field path>": "<message>"}}}``, the shape
    the web client turns into ``ApiError.fields``.
    """
    try:
        yield
    except ProfileValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code, "message": error.message, "fields": error.fields},
        ) from error


router = APIRouter(prefix="/api/profile", tags=["profile"], dependencies=[Depends(field_errors)])


def get_profile_service(session: AsyncSession = Depends(get_session)) -> ProfileService:
    return ProfileService(session)


@router.post("", response_model=ProfileRead, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreate, svc: ProfileService = Depends(get_profile_service)
) -> ProfileRead:
    return await svc.create(body)


@router.get("", response_model=ProfileRead)
async def read_profile(svc: ProfileService = Depends(get_profile_service)) -> ProfileRead:
    return await svc.read()


@router.patch("", response_model=ProfileRead)
async def update_profile(
    body: ProfileUpdate, svc: ProfileService = Depends(get_profile_service)
) -> ProfileRead:
    return await svc.update(body)


@router.post("/skills", response_model=ProfileRead, status_code=status.HTTP_201_CREATED)
async def add_skill(
    body: CurrentSkillCreate, svc: ProfileService = Depends(get_profile_service)
) -> ProfileRead:
    return await svc.add_skill(body)


@router.patch("/skills/{skill_id}", response_model=ProfileRead)
async def update_skill(
    skill_id: uuid.UUID,
    body: CurrentSkillUpdate,
    svc: ProfileService = Depends(get_profile_service),
) -> ProfileRead:
    return await svc.update_skill(skill_id, body)


@router.delete("/skills/{skill_id}", response_model=ProfileRead)
async def delete_skill(
    skill_id: uuid.UUID, svc: ProfileService = Depends(get_profile_service)
) -> ProfileRead:
    return await svc.delete_skill(skill_id)


@router.post("/targets", response_model=ProfileRead, status_code=status.HTTP_201_CREATED)
async def add_target(
    body: TargetSkillCreate, svc: ProfileService = Depends(get_profile_service)
) -> ProfileRead:
    return await svc.add_target(body)


@router.patch("/targets/{target_id}", response_model=ProfileRead)
async def update_target(
    target_id: uuid.UUID,
    body: TargetSkillUpdate,
    svc: ProfileService = Depends(get_profile_service),
) -> ProfileRead:
    return await svc.update_target(target_id, body)


@router.delete("/targets/{target_id}", response_model=ProfileRead)
async def delete_target(
    target_id: uuid.UUID, svc: ProfileService = Depends(get_profile_service)
) -> ProfileRead:
    return await svc.delete_target(target_id)
