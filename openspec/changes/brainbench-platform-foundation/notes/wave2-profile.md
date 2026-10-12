# Wave 2 notes: learner profile (tasks 4.1 to 4.3)

Branch `wave2-profile`, branched from `main` at `c146c9a`. Tasks 4.1, 4.2 and 4.3 are checked in `tasks.md`. Commits, in order:

| Commit | Content |
| --- | --- |
| `feat(profile): profile API with skills, targets, preferences and versioning (4.1)` | `app/models/profile.py`, `app/services/profile.py`, `app/api/v1/profile.py`, router registration, contract, service and model tests |
| `feat(profile): profile page with skill and target forms (4.2)` | `src/api/profile.ts`, `src/pages/profile/*`, one `sections` entry, web tests |
| `feat(profile): tests for every learner-profile validation scenario (4.3)` | one API test per spec scenario, plus service tests for the achieved-target rules |

No migration. No new dependency. No config field. The existing `Profile`, `CurrentSkill` and `TargetSkill` tables and the `SKILL_LEVELS`, `TARGET_LEVELS`, `PRIORITIES` and `DEPTHS` constants were enough.

## What I verified and how

- `just lint`: ruff check, ruff format --check, mypy strict, oxlint, prettier --check, `tsc --noEmit`. All green.
- `just test`: 122 Python tests and 38 web tests pass. `just test-py-eager`: 122 passed.
- Spec scenarios, one API test each (`apps/api/tests/api/v1/test_profile.py`, "learner-profile spec scenarios" block):
  - Profile created with required fields; second profile rejected (409 `profile-exists`, the message contains the existing id, the first profile is unchanged).
  - Invalid time budget rejected on create (0, -1, -0.5) and on update (0, -3). The 422 names `weekly_budget_hours`, and nothing is stored or bumped.
  - Current skill added. Duplicate skill name rejected, ignoring case and surrounding whitespace (`linear algebra`, `Linear Algebra`, `  LINEAR ALGEBRA  `), on add, on rename, inside an inline create, and for targets. The existing entry and the version stay unchanged.
  - Target skill added. Target at or below the current level rejected (below and equal) with the "must be higher than your current level" message on `target_level`. Also covered: lowering an existing target, an inline create, and raising a current skill to an active target's level.
  - Preferences default to all formats, `intuition-first`, `en`. An update to `{video}` and `rigor-first` is reflected on the next read. Bad formats, depth or language give a 422 naming the field.
  - Version: adding a target to a profile at version 3 gives 4; a read returns the current version; a rejected change or a no-op PATCH keeps the version.
- `promote_target` unit tests (`apps/api/tests/services/test_profile.py`): adds the current skill at the target level, marks the target achieved, bumps the version; raises an existing lower skill instead of adding a duplicate; is idempotent on an achieved target; raises `TargetMissing` for an unknown id.
- Web tests (`apps/web/tests/pages/profile/ProfilePage.test.tsx`): the create flow, and per-field rendering of API errors. A budget error lands on the budget field (`aria-invalid`). `preferences.formats.0` lands on the formats group. `duplicate-skill` lands on the skill name and the dialog stays open. `target-not-above-current` lands on Target level. An error with no `fields` shows as an alert. `apps/web/tests/api/profile.test.ts` checks the request paths and that `getProfile` returns `null` on `profile-missing`, against a real `Bun.serve` upstream.
- Smoke, on my ports. Postgres from `infra/compose.dev.yml` on 54304, migrations applied, uvicorn on 8004, `bun --hot` on 3004 with `API_URL=http://127.0.0.1:8004`. In Orca's browser:
  1. Completed the wizard and opened `/profile`, which showed the create form.
  2. Submitted a budget of 0. "Input should be greater than 0" appeared under the budget field.
  3. Created the profile with 6 hours, formats video and figure, `rigor-first`. Version 1.
  4. Added Linear algebra (working) and Python (expert). Version 3.
  5. Added `  LINEAR algebra ` as a current skill. "'LINEAR algebra' is already one of your current skills." appeared under the Skill field and the dialog stayed open.
  6. Added a Linear algebra target at Working. "The target level must be higher than your current level: 'Linear algebra' is already at working." appeared under Target level.
  7. Changed the target to Proficient and High priority, with a reason. Version 4.

  `psql` showed one profile row (`["video", "figure"]`, `rigor-first`, version 4), both skills and the target. `GET /api/profile` without the cookie returned 401. Afterwards I ran `docker compose down -v`, removed `./data`, stopped the servers and closed the tab. `git status` is clean.
- Orca's `click` does not scroll an element into view: "Add target" sat below the fold and the click did nothing until I ran `orca scroll`. This is a browser-driver detail, not an app bug.

## Shared-file touches (exact lines)

- `apps/api/app/api/v1/__init__.py`: the import line became `from app.api.v1 import auth, health, profile`, because ruff's isort merges same-module imports, so a separate import line is not possible. I added `router.include_router(profile.router)` after `router = APIRouter(...)`. Every section edits that same import line, so the merge needs a union of the module names.
- `apps/web/src/routes.tsx`: added `import PersonIcon from '@mui/icons-material/Person'` after the `CableIcon` import, and appended one `sections` entry `{ path: 'profile', label: 'Profile', icon: PersonIcon, Page: lazy(() => import('./pages/profile/ProfilePage')) }`.
- `openspec/.../tasks.md`: only the 4.1, 4.2 and 4.3 checkboxes.
- I did not touch `invalidations.ts`. Profile changes come from HTTP mutations, not job events. If a later section emits `profile.updated` (for example 8.2 from a worker), the default prefix rule already invalidates `['profile', ...]`.

## Blocked or suggested changes in files I do not own

Nothing blocked. One suggestion for the integrator: the shared error handler (`app/api/errors.py`) emits only `{code, message}`. Section 4 needs per-field messages for rules checked against stored data, so `app/api/v1/profile.py` installs a router-level yield dependency, `field_errors()`. It turns `ProfileValidationError` into a 422 with `{"detail": {"code", "message", "fields": {path: message}}}`, which `toApiError` already maps to `ApiError.fields`. If other sections need the same thing, a cleaner shared version would add an optional `fields` to `DomainError` and have `domain_error_handler` include it (and use 422 for a validation subclass). `field_errors()` would then be deleted. Without that dependency, `ProfileValidationError` falls back to the global handler as a 400 `{code, message}`.

## Public surface for sections 6, 7, 8, 9 and 10

### HTTP (all under the authenticated `router`, prefix `/api/profile`)

Every mutation returns the full `ProfileRead`.

| Method and path | Body | Success | Errors |
| --- | --- | --- | --- |
| `POST /api/profile` | `ProfileCreate` | 201 | 409 `profile-exists`; 422 Pydantic list; 422 `duplicate-skill` / `target-not-above-current` with fields `current_skills.<i>.name`, `target_skills.<i>.name`, `target_skills.<i>.target_level` |
| `GET /api/profile` | none | 200 | 404 `profile-missing` |
| `PATCH /api/profile` | `ProfileUpdate` (omitted fields unchanged) | 200 | 404, 422 |
| `POST /api/profile/skills` | `CurrentSkillCreate {name, level}` | 201 | 404 `profile-missing`; 422 `duplicate-skill` (`name`), `target-not-above-current` (`level`) |
| `PATCH /api/profile/skills/{id}` | `CurrentSkillUpdate {name?, level?}` | 200 | 404 `skill-missing`; 422 as above |
| `DELETE /api/profile/skills/{id}` | none | 200 | 404 `skill-missing` |
| `POST /api/profile/targets` | `TargetSkillCreate {name, target_level, priority, reason?}` | 201 | 422 `duplicate-skill` (`name`), `target-not-above-current` (`target_level`) |
| `PATCH /api/profile/targets/{id}` | `TargetSkillUpdate` (all optional; `reason: null` clears it) | 200 | 404 `target-missing`; 422 as above |
| `DELETE /api/profile/targets/{id}` | none | 200 | 404 `target-missing` |

### Contracts: `app/models/profile.py`

- `ProfileRead {id, display_name, background, weekly_budget_hours: float, preferences: Preferences, current_skills: list[CurrentSkillRead], target_skills: list[TargetSkillRead], version, created_at, updated_at}`.
- `Preferences {formats: list["video"|"figure"|"text"] (non-empty, deduplicated, canonical order), depth: "intuition-first"|"rigor-first", language: BCP-47-like tag}`, defaults all formats, `intuition-first`, `en`.
- `CurrentSkillRead {id, name, level}`; `TargetSkillRead {id, name, target_level, priority, reason, achieved_at}`.
- Literal types `SkillLevel`, `TargetLevel`, `Priority`, `Depth`, `ContentFormat`. A test asserts they equal the `schema.py` tuples. `CONTENT_FORMATS = ("video", "figure", "text")` lives here; `schema.py` has no constant for it.
- `weekly_budget_hours` is hours as a float, `> 0` and `<= 168`. Section 8 weekly pace and section 6 packing convert it to minutes (`* 60`).
- Ordering: current skills by normalized name; targets by priority (high, medium, low), then name.

### Service: `app/services/profile.py`

The module functions flush and never commit; the caller owns the transaction. `ProfileService` (the HTTP routes) commits.

```python
async def get_profile(session: AsyncSession) -> Profile | None
async def require_profile(session: AsyncSession) -> Profile          # raises ProfileMissing (NotFound, code "profile-missing")
async def list_current_skills(session, profile_id: UUID) -> list[CurrentSkill]
async def list_target_skills(session, profile_id: UUID) -> list[TargetSkill]
async def read_profile(session: AsyncSession, profile: Profile) -> ProfileRead   # full contract; safe after a flush
async def promote_target(session: AsyncSession, target_id: UUID) -> Profile
def normalize_name(name: str) -> str        # " ".join(name.split()).casefold(), stored in name_normalized
def level_rank(level: str) -> int           # index in SKILL_LEVELS; target levels compare on the same scale
def bump_version(profile: Profile) -> None
class ProfileService(session): read, create, update, add_skill, update_skill, delete_skill, add_target, update_target, delete_target
errors: ProfileMissing, ProfileExists, SkillMissing, TargetMissing (NotFound/Conflict),
        ProfileValidationError(DomainError) with .fields, DuplicateSkill, TargetNotAboveCurrent
```

- `promote_target` (for 8.2): adds the target's skill to current skills at `target_level`, or raises an existing lower current skill to it. It then sets `target.achieved_at`, bumps `profile.version` and flushes. It is a no-op returning the profile when the target is already achieved, and raises `TargetMissing` for an unknown id or another profile's target. Call it inside the same transaction that records the session, then commit.
- Rules: names are unique per list (current, targets) by `normalize_name`. The two lists can share a name. While a target is active (`achieved_at IS NULL`), its level must be above the same-named current skill. Adding or editing a target checks this against current skills; adding or editing a current skill checks it against active targets. Achieved targets do not cap current levels. Changing an achieved target's goal (normalized name or level) clears `achieved_at`; a case-only rename or a priority or reason edit keeps it.
- Versioning: every effective change bumps `version` by 1: create starts at 1, then any PATCH field that changes, any skill or target add, edit or delete, and `promote_target`. Rejected requests and no-op PATCHes do not bump. Curation (6.6) compares `profiles.version` with `curation_runs.profile_version` for the stale flag.
- `get_profile` is what the `get_profile` agent tool (6.1, 9.1) should call; `read_profile` gives the serializable view.

### Web

- `src/api/profile.ts`:
  - Value-set arrays and their types: `SKILL_LEVELS`, `TARGET_LEVELS`, `PRIORITIES`, `DEPTHS`, `CONTENT_FORMATS`.
  - Types named after the FastAPI models: `ProfileRead`, `CurrentSkillRead`, `TargetSkillRead`, `Preferences`, `ProfileCreate`, `ProfileUpdate`, `CurrentSkillCreate/Update`, `TargetSkillCreate/Update`.
  - Functions: `getProfile(): Promise<ProfileRead | null>` (`null` on `profile-missing`), `createProfile`, `updateProfile`, `addSkill`, `updateSkill(id, body)`, `deleteSkill(id)`, `addTarget`, `updateTarget(id, body)`, `deleteTarget(id)`.
- Query keys: `profileKeys.all = ['profile']`, `profileKeys.detail() = ['profile', 'detail']`. Mutations store the returned profile with `setQueryData(profileKeys.detail(), ...)`. A section that changes the profile server-side should invalidate `profileKeys.all` or emit a `profile.*` event.
- Route `/profile` (nav label "Profile"). Page `src/pages/profile/ProfilePage.tsx`, plus `ProfileForm`, `SkillsSection`, `SkillDialog`, `TargetsSection`, `TargetDialog`, `useProfile.ts` (`useProfileQuery`, `useProfileMutation`), `labels.ts` and `fieldErrors.ts`.
- `applyFieldErrors(error, fields, setError)` maps `ApiError.fields` onto react-hook-form fields, falling back to the closest ancestor path, and returns the error left for a form alert. It is section-local for now. Move it to `src/lib` if a second section wants it.
- Test support: `tests/support/profileMock.ts` exports `profileApi`, where each function is a `mock` delegating to the real implementation until a test overrides it, and `reset()` restores the real ones. Any test that renders routed pages alongside profile tests should import it (the same Bun shared-registry rule as `authMock`). Snapshot module functions before `mock.module`: Bun rewrites the live namespace, so calling `real.fn` after mocking recurses.
