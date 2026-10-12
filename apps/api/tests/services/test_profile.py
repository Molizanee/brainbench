import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import (
    CurrentSkillCreate,
    CurrentSkillUpdate,
    ProfileCreate,
    TargetSkillCreate,
    TargetSkillUpdate,
)
from app.services.profile import (
    DuplicateSkill,
    ProfileMissing,
    ProfileService,
    TargetMissing,
    TargetNotAboveCurrent,
    get_profile,
    normalize_name,
    promote_target,
    read_profile,
    require_profile,
)


def profile_body(**overrides: object) -> ProfileCreate:
    return ProfileCreate.model_validate(
        {"display_name": "Ada", "weekly_budget_hours": 5, **overrides}
    )


def test_normalize_name_ignores_case_and_whitespace() -> None:
    assert normalize_name("  Linear   Algebra ") == normalize_name("linear algebra")
    assert normalize_name("Straße") == normalize_name("STRASSE")


async def test_get_and_require_profile(session: AsyncSession) -> None:
    assert await get_profile(session) is None
    with pytest.raises(ProfileMissing) as missing:
        await require_profile(session)
    assert missing.value.code == "profile-missing"

    created = await ProfileService(session).create(profile_body())
    profile = await require_profile(session)
    assert profile.id == created.id
    assert await get_profile(session) is profile


async def test_promote_target_adds_current_skill(session: AsyncSession) -> None:
    svc = ProfileService(session)
    created = await svc.create(
        profile_body(
            target_skills=[
                {"name": "Distributed consensus", "target_level": "proficient", "priority": "high"}
            ]
        )
    )
    [target] = created.target_skills

    profile = await promote_target(session, target.id)
    await session.commit()

    view = await read_profile(session, profile)
    assert view.version == created.version + 1
    assert [(s.name, s.level) for s in view.current_skills] == [
        ("Distributed consensus", "proficient")
    ]
    assert view.target_skills[0].achieved_at is not None


async def test_promote_target_raises_existing_skill(session: AsyncSession) -> None:
    svc = ProfileService(session)
    created = await svc.create(
        profile_body(
            current_skills=[{"name": "raft", "level": "novice"}],
            target_skills=[{"name": "Raft", "target_level": "expert", "priority": "low"}],
        )
    )

    profile = await promote_target(session, created.target_skills[0].id)
    await session.commit()

    view = await read_profile(session, profile)
    assert [(s.name, s.level) for s in view.current_skills] == [("raft", "expert")]


async def test_promote_target_is_idempotent(session: AsyncSession) -> None:
    created = await ProfileService(session).create(
        profile_body(target_skills=[{"name": "Go", "target_level": "working", "priority": "low"}])
    )
    target_id = created.target_skills[0].id
    await promote_target(session, target_id)
    profile = await promote_target(session, target_id)
    await session.commit()

    view = await read_profile(session, profile)
    assert view.version == created.version + 1
    assert len(view.current_skills) == 1


async def test_promote_target_unknown_id(session: AsyncSession) -> None:
    await ProfileService(session).create(profile_body())
    with pytest.raises(TargetMissing):
        await promote_target(session, uuid.uuid4())


async def test_every_change_increments_version(session: AsyncSession) -> None:
    svc = ProfileService(session)
    view = await svc.create(profile_body())
    assert view.version == 1

    view = await svc.add_skill(CurrentSkillCreate(name="Python", level="working"))
    assert view.version == 2
    view = await svc.update_skill(view.current_skills[0].id, CurrentSkillUpdate(level="expert"))
    assert view.version == 3
    view = await svc.add_target(
        TargetSkillCreate(name="Rust", target_level="working", priority="medium")
    )
    assert view.version == 4
    view = await svc.delete_target(view.target_skills[0].id)
    assert view.version == 5
    view = await svc.delete_skill(view.current_skills[0].id)
    assert view.version == 6


async def test_achieved_target_does_not_cap_current_level(session: AsyncSession) -> None:
    svc = ProfileService(session)
    created = await svc.create(
        profile_body(target_skills=[{"name": "Go", "target_level": "working", "priority": "low"}])
    )
    await promote_target(session, created.target_skills[0].id)
    await session.commit()

    view = await svc.read()
    skill = view.current_skills[0]
    view = await svc.update_skill(skill.id, CurrentSkillUpdate(level="expert"))
    assert view.current_skills[0].level == "expert"


async def test_changing_an_achieved_goal_reopens_it(session: AsyncSession) -> None:
    svc = ProfileService(session)
    created = await svc.create(
        profile_body(target_skills=[{"name": "Go", "target_level": "working", "priority": "low"}])
    )
    target_id = created.target_skills[0].id
    await promote_target(session, target_id)
    await session.commit()

    # A case-only rename is the same goal: still achieved, no level check against "go".
    renamed = await svc.update_target(target_id, TargetSkillUpdate(name="go"))
    assert renamed.target_skills[0].achieved_at is not None
    view = await svc.update_target(target_id, TargetSkillUpdate(target_level="expert"))
    assert view.target_skills[0].achieved_at is None

    priority_only = await svc.update_target(target_id, TargetSkillUpdate(priority="high"))
    assert priority_only.version == view.version + 1


async def test_rule_violations_carry_field_paths(session: AsyncSession) -> None:
    svc = ProfileService(session)
    await svc.create(profile_body(current_skills=[{"name": "SQL", "level": "expert"}]))

    with pytest.raises(DuplicateSkill) as duplicate:
        await svc.add_skill(CurrentSkillCreate(name="sql", level="novice"))
    assert duplicate.value.fields == {"name": duplicate.value.message}

    with pytest.raises(TargetNotAboveCurrent) as below:
        await svc.add_target(TargetSkillCreate(name="SQL", target_level="expert", priority="low"))
    assert below.value.fields.keys() == {"target_level"}
    assert below.value.code == "target-not-above-current"
