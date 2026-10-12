"""Learner profile rules (learner-profile spec).

One profile per instance. Skill names are unique per list, compared ignoring case and
whitespace. A target skill whose name matches a current skill must sit above that skill's level
while the target is active (not yet achieved). Every change bumps ``profiles.version``, which
curation records so plans can be flagged stale.

Module functions (``get_profile``, ``require_profile``, ``read_profile``, ``promote_target``) are
for other services and flush without committing; ``ProfileService`` backs the HTTP routes and
commits.
"""

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, DomainError, NotFound
from app.db.schema import PRIORITIES, SKILL_LEVELS, CurrentSkill, Profile, TargetSkill
from app.models.profile import (
    CurrentSkillCreate,
    CurrentSkillRead,
    CurrentSkillUpdate,
    Preferences,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    TargetSkillCreate,
    TargetSkillRead,
    TargetSkillUpdate,
)

log = logging.getLogger(__name__)


class ProfileMissing(NotFound):
    code = "profile-missing"


class ProfileExists(Conflict):
    code = "profile-exists"


class SkillMissing(NotFound):
    code = "skill-missing"


class TargetMissing(NotFound):
    code = "target-missing"


class ProfileValidationError(DomainError):
    """A rule checked against the stored profile failed; ``fields`` maps field path to message."""

    code = "invalid-profile"

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.fields = {field: message}


class DuplicateSkill(ProfileValidationError):
    code = "duplicate-skill"


class TargetNotAboveCurrent(ProfileValidationError):
    code = "target-not-above-current"


def normalize_name(name: str) -> str:
    """Comparison key for skill names: case and surrounding or repeated whitespace ignored."""
    return " ".join(name.split()).casefold()


def level_rank(level: str) -> int:
    """Position in ``SKILL_LEVELS`` (target levels are a suffix of it, so ranks compare)."""
    return SKILL_LEVELS.index(level)


def _check_unique_current(
    name: str, skills: Iterable[CurrentSkill], *, field: str, exclude: uuid.UUID | None = None
) -> None:
    key = normalize_name(name)
    if any(s.name_normalized == key and s.id != exclude for s in skills):
        raise DuplicateSkill(f"'{name}' is already one of your current skills.", field=field)


def _check_unique_target(
    name: str, targets: Iterable[TargetSkill], *, field: str, exclude: uuid.UUID | None = None
) -> None:
    key = normalize_name(name)
    if any(t.name_normalized == key and t.id != exclude for t in targets):
        raise DuplicateSkill(f"'{name}' is already one of your target skills.", field=field)


def _check_target_above(
    name: str, target_level: str, skills: Iterable[CurrentSkill], *, field: str
) -> None:
    """A new or changed target must exceed the current level of a same-named skill."""
    key = normalize_name(name)
    for skill in skills:
        if skill.name_normalized == key and level_rank(target_level) <= level_rank(skill.level):
            raise TargetNotAboveCurrent(
                f"The target level must be higher than your current level: '{skill.name}' is "
                f"already at {skill.level}.",
                field=field,
            )


def _check_current_below_targets(
    name: str, level: str, targets: Iterable[TargetSkill], *, field: str
) -> None:
    """A new or changed current skill must stay below an active target of the same name."""
    key = normalize_name(name)
    for target in targets:
        if (
            target.achieved_at is None
            and target.name_normalized == key
            and level_rank(target.target_level) <= level_rank(level)
        ):
            raise TargetNotAboveCurrent(
                f"Your target for '{target.name}' is {target.target_level}; the current level "
                "must stay below it. Raise or remove the target first.",
                field=field,
            )


def _priority_rank(priority: str) -> int:
    return PRIORITIES.index(priority)


async def get_profile(session: AsyncSession) -> Profile | None:
    """The instance's profile, or ``None`` before it is created."""
    result = await session.scalars(select(Profile).limit(1))
    return result.first()


async def require_profile(session: AsyncSession) -> Profile:
    """The instance's profile; raises ``ProfileMissing`` (404 ``profile-missing``) when absent."""
    profile = await get_profile(session)
    if profile is None:
        raise ProfileMissing("No learner profile exists yet; create one first.")
    return profile


async def list_current_skills(session: AsyncSession, profile_id: uuid.UUID) -> list[CurrentSkill]:
    """Current skills ordered by name."""
    result = await session.scalars(
        select(CurrentSkill)
        .where(CurrentSkill.profile_id == profile_id)
        .order_by(CurrentSkill.name_normalized)
    )
    return list(result)


async def list_target_skills(session: AsyncSession, profile_id: uuid.UUID) -> list[TargetSkill]:
    """Target skills ordered by priority (high first), then name."""
    result = await session.scalars(select(TargetSkill).where(TargetSkill.profile_id == profile_id))
    return sorted(result, key=lambda t: (_priority_rank(t.priority), t.name_normalized))


async def read_profile(session: AsyncSession, profile: Profile) -> ProfileRead:
    """The full profile contract (skills, targets, preferences, version)."""
    # A flush expires server-side timestamps; load them here rather than lazily (async).
    await session.refresh(profile, ["created_at", "updated_at"])
    skills = await list_current_skills(session, profile.id)
    targets = await list_target_skills(session, profile.id)
    return ProfileRead(
        id=profile.id,
        display_name=profile.display_name,
        background=profile.background,
        weekly_budget_hours=profile.weekly_budget_hours,
        preferences=Preferences.model_validate(
            {
                "formats": profile.preferred_formats,
                "depth": profile.depth,
                "language": profile.language,
            }
        ),
        current_skills=[CurrentSkillRead.model_validate(s) for s in skills],
        target_skills=[TargetSkillRead.model_validate(t) for t in targets],
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def bump_version(profile: Profile) -> None:
    profile.version += 1


async def promote_target(session: AsyncSession, target_id: uuid.UUID) -> Profile:
    """Mark a target achieved and record its skill as a current skill at the target level.

    The current skill is added, or raised to the target level when it already exists. Bumps the
    profile version. Calling it on an achieved target is a no-op. Flushes; the caller commits.
    """
    profile = await require_profile(session)
    target = await session.get(TargetSkill, target_id)
    if target is None or target.profile_id != profile.id:
        raise TargetMissing(f"Target skill {target_id} does not exist.")
    if target.achieved_at is not None:
        return profile

    skills = await list_current_skills(session, profile.id)
    existing = next((s for s in skills if s.name_normalized == target.name_normalized), None)
    if existing is None:
        session.add(
            CurrentSkill(
                profile_id=profile.id,
                name=target.name,
                name_normalized=target.name_normalized,
                level=target.target_level,
            )
        )
    elif level_rank(existing.level) < level_rank(target.target_level):
        existing.level = target.target_level
    target.achieved_at = datetime.now(UTC)
    bump_version(profile)
    await session.flush()
    log.info("target %s promoted to current skill at %s", target.id, target.target_level)
    return profile


class ProfileService:
    """HTTP-facing operations: each mutation validates, bumps the version, and commits."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _commit(self, profile: Profile) -> ProfileRead:
        await self.session.commit()
        return await read_profile(self.session, profile)

    async def read(self) -> ProfileRead:
        return await read_profile(self.session, await require_profile(self.session))

    async def create(self, body: ProfileCreate) -> ProfileRead:
        existing = await get_profile(self.session)
        if existing is not None:
            raise ProfileExists(f"A learner profile already exists (id {existing.id}).")

        profile = Profile(
            id=uuid.uuid4(),
            display_name=body.display_name,
            background=body.background,
            weekly_budget_hours=body.weekly_budget_hours,
            preferred_formats=list(body.preferences.formats),
            depth=body.preferences.depth,
            language=body.preferences.language,
            version=1,
        )
        skills: list[CurrentSkill] = []
        for i, item in enumerate(body.current_skills):
            _check_unique_current(item.name, skills, field=f"current_skills.{i}.name")
            skills.append(
                CurrentSkill(
                    id=uuid.uuid4(),
                    profile_id=profile.id,
                    name=item.name,
                    name_normalized=normalize_name(item.name),
                    level=item.level,
                )
            )
        targets: list[TargetSkill] = []
        for i, target in enumerate(body.target_skills):
            _check_unique_target(target.name, targets, field=f"target_skills.{i}.name")
            _check_target_above(
                target.name, target.target_level, skills, field=f"target_skills.{i}.target_level"
            )
            targets.append(
                TargetSkill(
                    id=uuid.uuid4(),
                    profile_id=profile.id,
                    name=target.name,
                    name_normalized=normalize_name(target.name),
                    target_level=target.target_level,
                    priority=target.priority,
                    reason=target.reason,
                )
            )
        # The children reference ``profile.id`` with no ORM relationship to order the inserts,
        # so the profile row is flushed first (Postgres enforces the key; SQLite does not).
        self.session.add(profile)
        try:
            await self.session.flush()
            self.session.add_all([*skills, *targets])
            await self.session.flush()
        except IntegrityError as exc:
            # Lost a race with a concurrent create: the singleton constraint fired.
            await self.session.rollback()
            existing = await get_profile(self.session)
            if existing is None:
                raise
            raise ProfileExists(f"A learner profile already exists (id {existing.id}).") from exc
        log.info("profile %s created", profile.id)
        return await self._commit(profile)

    async def update(self, body: ProfileUpdate) -> ProfileRead:
        profile = await require_profile(self.session)
        changes: dict[str, object] = {}
        if body.display_name is not None:
            changes["display_name"] = body.display_name
        if body.background is not None:
            changes["background"] = body.background
        if body.weekly_budget_hours is not None:
            changes["weekly_budget_hours"] = body.weekly_budget_hours
        if body.preferences is not None:
            prefs = body.preferences
            if prefs.formats is not None:
                changes["preferred_formats"] = list(prefs.formats)
            if prefs.depth is not None:
                changes["depth"] = prefs.depth
            if prefs.language is not None:
                changes["language"] = prefs.language

        changed = {k: v for k, v in changes.items() if getattr(profile, k) != v}
        if changed:
            for key, value in changed.items():
                setattr(profile, key, value)
            bump_version(profile)
            log.info("profile %s updated (%s)", profile.id, ", ".join(sorted(changed)))
        return await self._commit(profile)

    # --- current skills -----------------------------------------------------------------

    async def _skill(self, profile: Profile, skill_id: uuid.UUID) -> CurrentSkill:
        skill = await self.session.get(CurrentSkill, skill_id)
        if skill is None or skill.profile_id != profile.id:
            raise SkillMissing(f"Current skill {skill_id} does not exist.")
        return skill

    async def add_skill(self, body: CurrentSkillCreate) -> ProfileRead:
        profile = await require_profile(self.session)
        skills = await list_current_skills(self.session, profile.id)
        targets = await list_target_skills(self.session, profile.id)
        _check_unique_current(body.name, skills, field="name")
        _check_current_below_targets(body.name, body.level, targets, field="level")
        self.session.add(
            CurrentSkill(
                profile_id=profile.id,
                name=body.name,
                name_normalized=normalize_name(body.name),
                level=body.level,
            )
        )
        bump_version(profile)
        return await self._commit(profile)

    async def update_skill(self, skill_id: uuid.UUID, body: CurrentSkillUpdate) -> ProfileRead:
        profile = await require_profile(self.session)
        skill = await self._skill(profile, skill_id)
        name = body.name if body.name is not None else skill.name
        level = body.level if body.level is not None else skill.level
        if (name, level) != (skill.name, skill.level):
            skills = await list_current_skills(self.session, profile.id)
            targets = await list_target_skills(self.session, profile.id)
            _check_unique_current(name, skills, field="name", exclude=skill.id)
            _check_current_below_targets(name, level, targets, field="level")
            skill.name, skill.name_normalized, skill.level = name, normalize_name(name), level
            bump_version(profile)
        return await self._commit(profile)

    async def delete_skill(self, skill_id: uuid.UUID) -> ProfileRead:
        profile = await require_profile(self.session)
        await self.session.delete(await self._skill(profile, skill_id))
        bump_version(profile)
        return await self._commit(profile)

    # --- target skills ------------------------------------------------------------------

    async def _target(self, profile: Profile, target_id: uuid.UUID) -> TargetSkill:
        target = await self.session.get(TargetSkill, target_id)
        if target is None or target.profile_id != profile.id:
            raise TargetMissing(f"Target skill {target_id} does not exist.")
        return target

    async def add_target(self, body: TargetSkillCreate) -> ProfileRead:
        profile = await require_profile(self.session)
        skills = await list_current_skills(self.session, profile.id)
        targets = await list_target_skills(self.session, profile.id)
        _check_unique_target(body.name, targets, field="name")
        _check_target_above(body.name, body.target_level, skills, field="target_level")
        self.session.add(
            TargetSkill(
                profile_id=profile.id,
                name=body.name,
                name_normalized=normalize_name(body.name),
                target_level=body.target_level,
                priority=body.priority,
                reason=body.reason,
            )
        )
        bump_version(profile)
        return await self._commit(profile)

    async def update_target(self, target_id: uuid.UUID, body: TargetSkillUpdate) -> ProfileRead:
        profile = await require_profile(self.session)
        target = await self._target(profile, target_id)
        name = body.name if body.name is not None else target.name
        level = body.target_level if body.target_level is not None else target.target_level
        priority = body.priority if body.priority is not None else target.priority
        reason = body.reason if "reason" in body.model_fields_set else target.reason

        goal_changed = (normalize_name(name), level) != (
            target.name_normalized,
            target.target_level,
        )
        if goal_changed:
            skills = await list_current_skills(self.session, profile.id)
            targets = await list_target_skills(self.session, profile.id)
            _check_unique_target(name, targets, field="name", exclude=target.id)
            _check_target_above(name, level, skills, field="target_level")
            # A changed goal is a new goal: it is no longer achieved.
            target.achieved_at = None
        if (name, level, priority, reason) != (
            target.name,
            target.target_level,
            target.priority,
            target.reason,
        ):
            target.name, target.name_normalized = name, normalize_name(name)
            target.target_level, target.priority, target.reason = level, priority, reason
            bump_version(profile)
        return await self._commit(profile)

    async def delete_target(self, target_id: uuid.UUID) -> ProfileRead:
        profile = await require_profile(self.session)
        await self.session.delete(await self._target(profile, target_id))
        bump_version(profile)
        return await self._commit(profile)
