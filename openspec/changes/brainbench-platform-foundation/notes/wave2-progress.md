# Wave 2 notes: learning progress (tasks 8.1 to 8.4)

Branch `wave2-progress`, from `c146c9a`. Tasks 8.1 to 8.4 are checked in `tasks.md`. Commits:

| Commit | Content |
| --- | --- |
| `feat(progress): log study sessions, derive topic status, progress summary with weekly pace (8.1)` | models, `services/{sessions,progress}.py`, router, API and service tests |
| `feat(progress): promote a target skill once every serving topic is mastered (8.2)` | `promote_achieved_targets`, wired into session logging, tests |
| `feat(progress): progress page and log-session dialog (8.3)` | `src/api/progress.ts`, `pages/progress/`, route entry, web tests |
| `feat(progress): scenario tests for status transitions, promotion and weekly pace (8.4)` | spec scenario tests |
| this notes file | |

No migration. No config field. No dependency added. No SSE event emitted.

## What I verified and how

- `just lint`: green (ruff, ruff format, mypy strict, oxlint, prettier, `tsc --noEmit`).
- `just test`: 103 Python tests (60 before), 40 web tests (25 before). `just test-py-eager`: 103 passed. The web suite prints 6 React `act(...)` warnings for `FormControl`; they are on `c146c9a` as well and do not come from this section.
- Smoke on Postgres (pgvector pg16 from `infra/compose.dev.yml` on `127.0.0.1:54308`, `alembic upgrade head`, uvicorn on :8308, `bun --hot server.ts` on :3008 proxying to it):
  - Seeded with `psql`: a profile (5 h/week), the target "Distributed consensus" (proficient, high), two topics with revisions, plan version 1 with both items serving the target. One item names the target by id and the other by name in another case (`"Distributed Consensus"`).
  - With curl through the Bun proxy: `minutes: 0` returned 422 at `body.minutes`. Raft: `understood` 45 gave in-progress, then `could-teach` 60 gave mastered, then `struggled` 15 gave in-progress again (regression), then `confident` 30 gave mastered. Log replication `confident` 60 returned `promoted_targets: [{"name": "Distributed consensus", "level": "proficient"}]`. In the DB, `current_skills` had `Distributed consensus | proficient`, `profiles.version` went 1 to 2, and `target_skills.achieved_at` was set. An unknown topic returned 404 `topic-not-found`.
  - `GET /api/progress?utc_offset_minutes=-180` returned the local week (Mon Oct 5 03:00Z to Oct 12 03:00Z), 210 of 300 minutes (70.0%), counts mastered 2 / in-progress 0 / not-started 0, and 0 minutes remaining.
  - Orca browser at `http://localhost:3008/progress`: login, then the page showed 70%, "3 h 30 min of 5 h", the target card with the Achieved chip, the counts, Studied 3 h 30 min and Remaining 0 min. Through the dialog: submitting empty showed "Log at least 1 minute" and "Choose how well you mastered it". Logging 30 min `Struggled` with a note on Log replication closed the dialog and refreshed the page to 80%, "4 h of 5 h", mastered 1 / in-progress 1, Remaining 2 h 30 min. The first screenshot showed the status chip overlapping the Log session button (`ListItem` `secondaryAction`). That was fixed before the 8.3 commit, and the second screenshot looked correct.
  - Teardown: servers stopped, `docker compose down -v`, `./data` removed, no `wave2-progress` containers left.

## Shared-file touches (exact lines)

- `apps/api/app/api/v1/__init__.py`: the import line changed from `from app.api.v1 import auth, health` to `from app.api.v1 import auth, health, progress` (ruff isort merges a separate import into this line), and this line was appended at the end:
  `router.include_router(progress.router)`
- `apps/web/src/routes.tsx`: added `import InsightsIcon from '@mui/icons-material/Insights'` after the `CableIcon` import, and appended one `sections` entry:
  `{ path: 'progress', label: 'Progress', icon: InsightsIcon, Page: lazy(() => import('./pages/progress/ProgressPage')) }`
- `tasks.md`: the 8.1 to 8.4 checkboxes only.
- Not touched: `apps/web/src/events/invalidations.ts` (no events), `apps/api/app/jobs/__init__.py`, `pyproject.toml` files, `package.json`, lockfiles, `justfile`, CI, `schema.py`, migrations, `config.py`.

## Blocked changes in never-edit files

None.

## Integration notes

- **Promotion duplicates section 4.** `app/services/progress.py::promote_achieved_targets` changes `Profile`, `CurrentSkill` and `TargetSkill` directly, because this section could not import `app/services/profile.py`. Its semantics: it adds the current skill at `target_level` with the target's `name` and `name_normalized`, or raises an existing lower entry to that level (it never lowers one); it sets `TargetSkill.achieved_at`; and it adds `Profile.version += 1` once per call, however many targets were promoted. Section 4 ships `promote_target` with the same semantics. The integrator can make `_add_current_skill` plus the version bump call section 4's `promote_target`.
- **`serves_targets` matching.** An item serves a target when an entry of `PlanItem.serves_targets` equals `str(target.id)` or, after `strip().casefold()`, equals the target's `name_normalized`. Section 6 can write either target ids (preferred) or names. "Topics serving a target" are the non-excluded items (`excluded = false`) of the latest `study_plans.version`. A target served by no topic is never promoted.
- **When promotion runs.** It runs after a logged session leaves its topic mastered. It does not run when a curation run or a plan edit produces a plan whose remaining serving topics are already all mastered. Section 6 should call `await promote_achieved_targets(session)` after it writes a new plan version, then commit.
- **No demotion.** A later regression changes the topic status but leaves `achieved_at` and the current skill in place; the spec defines no demotion.
- **Topic id resolution.** `POST /api/topics/{id}/sessions` follows `topics.merged_into` (up to 32 hops; a cycle is a 404), so a session logged on a merged topic lands on its survivor.
- **Topic query key.** On success, `LogSessionDialog` invalidates `['progress']` and `['topics', topicId]` (through `sessionLoggedKeys(topicId)` in `src/api/progress.ts`). When section 7 adds `topicKeys` in `src/api/topics.ts`, `sessionLoggedKeys` can return `topicKeys.detail(id)` in place of the literal, provided that key starts with `['topics', id]`.
- **Clock and ordering.** The service sets `studied_at` from the clock with microsecond precision. "Latest session" is ordered by `studied_at` then `created_at`, both descending.

## Public surface

### HTTP (authenticated `router`, tag `progress`)

- `POST /api/topics/{topic_id}/sessions` takes `StudySessionCreate {minutes: int (1..1440), rating: "struggled"|"understood"|"confident"|"could-teach", note?: str|null (max 4000, trimmed, empty becomes null)}` and returns 201 `SessionLogged {session: StudySessionRead, topic: TopicProgress, promoted_targets: PromotedTarget[]}`. Errors: 422 (FastAPI validation, `loc` `["body", "minutes"]` etc.), 404 `topic-not-found`, 401 without a session.
- `GET /api/progress?utc_offset_minutes=<int -840..840, default 0>` returns 200 `ProgressRead {plan_version, profile_version, minutes_studied, week: WeeklyPace, targets: TargetProgress[]}`. The offset is the viewer's local offset from UTC (UTC-3 is `-180`). The week runs from Monday 00:00 local to the next Monday, and its bounds are returned in UTC. Without a profile, `targets` is `[]` and `budget_minutes`/`used_pct` are `null`. Without a plan, targets are listed with zero counts and no topics.

### Models: `apps/api/app/models/progress.py`

`MasteryRating`, `TopicStatus` (PEP 695 `type` aliases of `Literal`s), `MAX_SESSION_MINUTES = 1440`, `StudySessionCreate`, `StudySessionRead` (`from_attributes`), `TopicProgress {topic_id, status, minutes_studied, session_count}`, `PromotedTarget {id, name, level}`, `SessionLogged`, `StatusCounts {mastered, in_progress, not_started}`, `TargetTopic {topic_id, title, status, minutes_studied, estimate_minutes}`, `TargetProgress {id, name, target_level, priority, achieved_at, counts, minutes_studied, minutes_remaining, topics}`, `WeeklyPace {week_start, week_end, minutes_studied, budget_minutes, used_pct}`, `ProgressRead`. `minutes_remaining` is the sum of `estimate_minutes` over the target's topics that are not mastered. `used_pct` is rounded to one decimal and can exceed 100.

### Services: `apps/api/app/services/progress.py` (for section 6 scoring and planner, and section 7's topic page)

```python
MASTERED_RATINGS = frozenset({"confident", "could-teach"})
type Clock = Callable[[], datetime]; def utc_now() -> datetime
def status_for(latest_rating: str | None) -> TopicStatus
async def topic_status(session, topic_id: UUID) -> str
async def session_history(session, topic_ids: Iterable[UUID]) -> dict[UUID, list[StudySession]]  # oldest first; every id present
async def mastered_topic_ids(session) -> set[UUID]
@dataclass(frozen=True) class TopicStats: status: TopicStatus; minutes: int; sessions: int
async def topic_stats(session, topic_ids: Iterable[UUID]) -> dict[UUID, TopicStats]               # every id present
async def current_plan(session) -> StudyPlan | None                                               # highest version
async def plan_topics(session, plan) -> list[ServingTopic]                                        # non-excluded, by position
def serves(topic: ServingTopic, target: TargetSkill) -> bool
async def promote_achieved_targets(session, *, now: Clock = utc_now) -> list[PromotedTarget]     # flushes; caller commits
def week_bounds(now: datetime, utc_offset_minutes: int) -> tuple[datetime, datetime]
class ProgressService(session, *, now: Clock = utc_now): async summary(*, utc_offset_minutes=0) -> ProgressRead
```

`apps/api/app/services/sessions.py`: `SessionService(session, *, now=utc_now).log(topic_id, StudySessionCreate) -> SessionLogged` (commits); `TopicNotFound(NotFound)` with code `topic-not-found`.

Router dependencies in `app/api/v1/progress.py`: `get_session_service`, `get_progress_service`.

### Web

- `src/api/progress.ts`: types mirroring the models above, `MASTERY_RATINGS`, `MAX_SESSION_MINUTES`, `progressKeys` (`all = ['progress']`, `summary(offset)`), `sessionLoggedKeys(topicId) -> QueryKey[]`, `localUtcOffsetMinutes()`, `getProgress(utcOffsetMinutes)`, `logSession(topicId, body)`.
- `src/pages/progress/components/LogSessionDialog.tsx`: `export function LogSessionDialog({ topicId, topicTitle?, open, onClose, onLogged?(result: SessionLogged) })`. It is self-contained (react-hook-form + zod, API 422 field errors on the field, other errors in an `Alert`) and invalidates the progress and topic queries on success. The topic page in section 7 mounts it as is.
- `src/pages/progress/ProgressPage.tsx` (default export, route `/progress`), `components/TargetCard.tsx`, `components/WeeklyPaceCard.tsx`, `format.ts` (`formatMinutes`, `ratingLabels`, `statusLabels`).
- Tests: `tests/support/progressMock.ts` is a module-boundary mock of `src/api/progress` in the `authMock` style (`progressApi.reset(data)`, `sampleProgress`, `emptyProgress`, `loggedResult`). Any later test file that renders the progress page or the dialog (for example section 7's topic page) must import it and call `progressApi.reset(...)`.
