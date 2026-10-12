# Wave 2 notes: job orchestration (tasks 2.1 to 2.5)

Branch `wave2-jobs`, from `main` at `c146c9a`. Tasks 2.1 to 2.5 are checked in `tasks.md`. Commits, in order:

| Commit | Content |
| --- | --- |
| `feat(jobs): claim loop ... (2.1)` | `app/services/{jobs,job_queue,job_events}.py`, `app/jobs/runner.py`, the `run()` loop in `app/worker.py`, claim gates and kill hooks in `app/jobs/registry.py`, config fields, Postgres claim tests |
| `feat(jobs): retry backoff ... (2.2)` | retry and deferral arithmetic tests, runner outcome tests |
| `feat(jobs): cancellation ... (2.3)` | cancel service tests, runner cancel tests (kill hook, grace cancel, late cancel) |
| `feat(jobs): job events over LISTEN/NOTIFY ... (2.4)` | `pg_notify` in `record_event`, `EventHub`, `GET /events`, `/api/jobs` endpoints, web `api/jobs.ts`, Jobs page, invalidations |
| `feat(jobs): job orchestration tests ... (2.5)` | crash-recovery and render-limit scenarios (unit and Postgres), `asyncio.wait` fix in the runner, `just jobs-integration`, CI job |
| `feat(jobs): wave 2 notes ...` | this file; `enqueue` sets `updated_at` (see "Public surface"); requeue log wording |

The runner (`app/jobs/runner.py`) and the lease-guarded transitions (`app/services/job_queue.py`) landed whole in the 2.1 commit, because the claim loop cannot run without recording outcomes. Commits 2.2 and 2.3 carry the tests that verify those parts, plus the small additions those tasks introduced (`JobCancelled`, `raise_if_cancelled`, kill hook tests).

## What I verified, and how

All on macOS arm64, Docker Desktop 29.2.1, uv 0.13.0, Bun 1.3.13, Python 3.14.8, Postgres `pgvector/pgvector:pg16` on `127.0.0.1:54302`.

- `just lint`: ruff, ruff format, mypy strict (71 files), oxlint, prettier, `tsc --noEmit`. Green.
- `just test`: 117 Python passed, 8 skipped (the Postgres tests, without `DATABASE_URL`); 34 web passed. `just test-py-eager`: same counts. The web suite ran three times with no flakes. It prints six React `act(...)` warnings, which come from the existing auth page tests: running the suite without my files prints the same six.
- `DATABASE_URL=postgresql+asyncpg://brainbench:brainbench@127.0.0.1:54302/brainbench just jobs-integration`: 8 passed. These tests cover:
  - SKIP LOCKED: a claim skips a row another transaction holds `FOR UPDATE` without waiting, and takes it once it is released.
  - Six concurrent claimers with an instance limit of 2: exactly two distinct jobs claimed.
  - Concurrent claimers with two videos and one noop: one video and the noop run, the second video stays queued. The same check passes with two `Runner`s.
  - LISTEN/NOTIFY: a notification arrives only after commit, and a rolled-back change never notifies. Two subscribers both receive each event, and the LISTEN connection closes with the last subscriber.
  - A runner whose job task is killed leaves the job `running`. A second runner, with its clock past the 2-minute stale cutoff, reaps the job and finishes it on attempt 2.
- Smoke, with the real worker entrypoint (`app.worker.main()`, including `prepare_sandboxes()` with `BRAINBENCH_DATA_HOST` and the real `brainbench/manim` probe) and uvicorn on `:8302`, both against the dev Postgres. A throwaway wrapper registered an extra `sleep` job type (sleeps N seconds, calls `raise_if_cancelled()` every second, reports progress every 10 s). Jobs were enqueued by a throwaway script that calls `app.services.jobs.enqueue`. Neither script is committed.
  - `GET /events` without the cookie returned 401. With the cookie, `curl -N` received `retry: 3000`, then for the `noop` job: `jobs.updated` + `jobs.event` for queued, running, progress "noop running", and ready. `GET /api/jobs/{id}` returned ready, attempt 1, created/started/finished timestamps, and 4 events.
  - Cancel: a running `sleep` job returned `running, cancel_requested: true, "cancel requested"` from `POST /api/jobs/{id}/cancel`. It became `cancelled` 6 s later (the worker log shows the monitor saw the flag 5 s after the claim and recorded cancelled 1 s after that). A second cancel returned 200 (idempotent). Cancelling a ready job returned 409 `job-finished`.
  - Worker kill: a `sleep` job was running on worker A. `kill -9` on the worker (23:22:47) left the job `running, attempt 1`. Worker B started at 23:22:50. At 23:24:51 its reaper requeued the job (event `requeued after its worker stopped heartbeating`, `data.worker` = worker A), and worker B claimed it as attempt 2 in the same second. The job's last heartbeat was its claim at 23:22:37, so this was 2 min 14 s after the last heartbeat and 2 min 4 s after the kill (stale cutoff 120 s, plus up to one 15 s reaper interval).
  - Graceful stop: `SIGTERM` to worker B while it ran a `sleep` job requeued that job at once (`queued`, attempt 1, progress `requeued: worker stopped`), and the worker logged `worker stopped` and exited.
  - Disconnect: killing the `curl -N` clients dropped `pg_stat_activity` LISTEN connections from 1 to 0 within a second.
  - Through the Bun server (`API_URL=http://localhost:8302 PORT=3002 bun server.ts`): `GET /events` returned 200 with `Content-Type: text/event-stream; charset=utf-8` and `Cache-Control: no-cache`, and streamed a noop job's four `jobs.updated`/`jobs.event` pairs live.
- Afterwards: `docker compose down -v` in the worktree, `./data` removed, smoke processes stopped.

## Behavior and decisions sections 6, 7, 9 and 10 should know

- Attempts: `attempt` is 0 while a job has never run. The first claim sets it to 1. A transient failure re-queues with `attempt + 1` and `run_after = now + 30 s * 2^(attempt-1)` (30 s, then 60 s); when `attempt == max_attempts` (3) the job fails with reason `retries-exhausted`. A provider-limit deferral and a graceful worker stop leave `attempt` unchanged. A reaped job (its worker stopped heartbeating) counts as an attempt; on the last attempt it fails with reason `worker-lost`, so a job that keeps killing its worker cannot loop forever.
- Deferral: `run_after = max(reset_at, now) + jitter(0..60 s)`, or `now + 300 s + jitter(0..60 s)` without a reset. A reset already in the past counts from now. The checkpoint saved is `ctx.checkpoint` at the moment the handler raised.
- Error reasons (`jobs.error_reason`): `PermanentJobError.reason` as raised (7.x uses `timeout`, `budget-exhausted`, and so on), `retries-exhausted`, `worker-lost`, `unknown-type` (no handler registered; failed at once), `cancelled`.
- Priority: higher `priority` runs first; equal priorities run in creation order.
- Limits: `settings.job_concurrency` (2) counts `running` jobs instance-wide, across worker processes. Per type, `video` and `figure` are each capped at `settings.render_concurrency` (1) by `job_queue.render_type_limits()`. The two caps are separate, so one video and one figure can run at the same time. If 7.x wants one shared render slot, change that function. Claims are serialized by a transaction-level advisory lock (`CLAIM_LOCK_KEY`), so the count and the claim are atomic; rows are still taken `FOR UPDATE SKIP LOCKED`.
- Reaper timing: every worker reaps every `job_reaper_interval_s` (15 s); a job is stale when `heartbeat_at` is older than `job_stale_after_s` (120 s). Heartbeats are written every 15 s. A crashed worker's job is therefore requeued between 120 s and 135 s after its last heartbeat. The spec says "within 2 minutes" and the design says "heartbeat older than 2 minutes"; I followed the design value, which can exceed the spec by up to one reaper interval. Lower `job_stale_after_s` to 105 if the spec wording is meant strictly. On SIGTERM/SIGINT the worker does not wait for the reaper: it runs kill hooks, cancels handlers, and requeues its running jobs at once (attempt unchanged, progress "requeued: worker stopped").
- Transactions: the handler's `ctx.session` is its own session. On success the worker records `ready` in that same session and commits, so whatever the handler wrote commits atomically with the status. On failure, deferral, cancellation, or shutdown the handler session is rolled back. Handlers that want intermediate state visible (a source moving from "extracting" to "embedding") may `await ctx.session.commit()` themselves; anything committed that way stays. `ctx.progress()` and `ctx.save_checkpoint()` use separate short transactions, so they survive a failed attempt.
- A handler that returns normally after `ctx.cancelled()` became true is treated as interrupted, not finished: its writes are rolled back. Raise (`ctx.raise_if_cancelled()`) or return; either is safe.
- Cancel wins races. If a cancel commits after the handler returned but before the success transaction, the success transaction sees `cancel_requested`, rolls back, and records `cancelled`. On cancellation the runner also deletes `settings.jobs_dir / <job id>` (partial outputs discarded).
- Event payloads are the job as loaded when the stream handles the notification, not a snapshot at event time. A fast job can therefore produce a `jobs.updated` whose status is already ahead of the paired `jobs.event`. The UI only uses them to invalidate queries, so this is harmless.

## Shared-file touches (exact)

- `apps/api/app/api/v1/__init__.py`: the import line became `from app.api.v1 import auth, events, health, jobs`, and two lines were added after `router = APIRouter(...)`: `router.include_router(jobs.router)` and `router.include_router(events.router)`. These are two routers from one section: `/api/jobs` and the bare `/events`.
- `apps/api/app/jobs/__init__.py`: not touched. `noop` was already registered, and `app/jobs/runner.py` is not a handler.
- `apps/api/app/core/config.py` (fields only): `job_heartbeat_s = 15.0`, `job_stale_after_s = 120.0`, `job_reaper_interval_s = 15.0`, `job_cancel_poll_s = 5.0`, `job_cancel_grace_s = 10.0`, `job_retry_base_s = 30.0`, `job_provider_wait_s = 300.0`, `job_provider_jitter_s = 60.0`. The worker's claim poll reuses the existing `worker_idle_sleep_s` (5 s).
- `apps/api/app/worker.py` (owned by section 2): `run()` keeps the schema guard and the handler log line. It then installs SIGTERM/SIGINT handlers that set a stop event and runs `Runner(SessionFactory, worker_id=f"{hostname}:{pid}").run(stop)`. `prepare_sandboxes()` and the startup order are unchanged.
- `apps/api/app/jobs/registry.py` and `app/jobs/errors.py`: additive only. The handler-facing methods are unchanged (see "Public surface").
- `apps/web/src/routes.tsx`: appended one `sections` entry `{ path: 'jobs', label: 'Jobs', icon: WorkHistoryIcon, Page: lazy(() => import('./pages/jobs/JobsPage')) }` and its icon import. `SectionRoute` is unchanged.
- `apps/web/src/events/invalidations.ts`: now exports `jobTypeInvalidations` and has two entries, `'jobs.updated'` and `'jobs.event'` (see below).
- `justfile`: appended the recipe `jobs-integration`. `.github/workflows/ci.yml`: appended the job `jobs-integration` (pgvector service, `just jobs-integration`), in the style of `migrations`.
- No dependency was added (`pyproject.toml`, `package.json` and the lockfiles are untouched).
- No migration. Every column used was already in `0001`.
- Postgres tests live in `apps/api/tests/integration/jobs/`, with their own `conftest.py`. I did not create `tests/integration/conftest.py`, to avoid colliding with other sections. Its fixtures (`pg_engine`, `pg_factory`) give each test a throwaway schema through `schema_translate_map`, so they never touch the target database's tables. They skip themselves without a Postgres `DATABASE_URL`. If several sections need the same fixtures, the integrator can hoist them to `tests/integration/conftest.py`.

## Changes needed in never-edit files

None.

## Public surface

### Enqueue, read, cancel: `app/services/jobs.py`

Sections 5, 6 and 7 create jobs only through `enqueue`.

```python
ACTIVE_STATUSES = ("queued", "running"); TERMINAL_STATUSES = ("ready", "failed", "cancelled", "skipped")
class JobNotFound(NotFound): code = "job-not-found"        # 404
class JobFinished(Conflict): code = "job-finished"        # 409
def utcnow() -> datetime; def as_utc(value: datetime) -> datetime   # SQLite returns naive datetimes

async def enqueue(session, type: str, payload: dict[str, Any], *, dedupe_key: str | None = None,
                  priority: int = 0, run_after: datetime | None = None, now: datetime | None = None) -> Job
async def get_job(session, job_id: UUID) -> Job                     # JobNotFound
async def list_jobs(session, *, statuses: Sequence[str] | None = None, type: str | None = None, limit: int = 50) -> list[Job]
async def list_events(session, job_id: UUID, *, limit: int = 100) -> list[JobEvent]   # oldest first
async def cancel(session, job_id: UUID, *, now: datetime | None = None) -> Job        # JobNotFound, JobFinished
class JobService(session): list_jobs(...), get_job(id) -> (Job, events), cancel(id)  # HTTP wrapper; commits
```

- `enqueue` and `cancel` flush and never commit. Commit in the caller, together with your own rows. The worker sees the job, and `/events` announces it, only after that commit.
- `enqueue(..., dedupe_key=k)` returns the existing queued or running job holding `k` (7.5's "request dedupe returns the existing job id"). A concurrent duplicate insert is caught on the partial unique index and also returns the existing job. Once the job finishes, the key is free again.
- `enqueue` records a `status`/`queued` event and sets `created_at`, `updated_at` and `run_after` itself, so the returned `Job` serializes after commit without a reload. `cancel` changes server-generated `updated_at`: call `await session.refresh(job)` before reading it after commit, as `JobService.cancel` does.
- `cancel` on a queued job sets `cancelled` immediately; the job is never started. On a running job it sets `cancel_requested`, and the worker finishes it as `cancelled` within about `job_cancel_poll_s` (5 s) plus your kill hook's time. A cancelled job is a no-op; any other finished job raises `JobFinished`. Section 5's "delete a source whose ingestion job is running" should call `cancel(session, source.job_id)`, ignore `JobFinished`, then delete the source. `jobs.id` is referenced by `sources.job_id` and `job_events.job_id`; `job_events` cascades.

### Handler side: `app/jobs/registry.py`, `app/jobs/errors.py`

```python
@dataclass class JobContext:   # fields as in wave 1, plus:
    kill_hooks: list[KillHook]                                   # filled by register_kill_hook
    def raise_if_cancelled(self) -> None                         # raises JobCancelled when cancelled()
    def register_kill_hook(self, hook: Callable[[], Awaitable[None]]) -> Callable[[], None]  # returns unregister
    async def run_kill_hooks(self) -> None                       # worker-side; runs each hook once, logs failures
type ClaimGate = Callable[[AsyncSession], Awaitable[bool]]
CLAIM_GATES: dict[str, ClaimGate]
def register_claim_gate(job_type: str) -> Callable[[ClaimGate], ClaimGate]   # ValueError on a second gate

class PermanentJobError(Exception)                               # unchanged
class JobCancelled(PermanentJobError)                            # reason "cancelled"
class ProviderLimitReached(Exception)                            # unchanged
class JobSkipped(Exception): __init__(message); .message        # status "skipped", progress = message
```

- 7.2/7.4 sandbox runner: right after starting a container, call `unregister = ctx.register_kill_hook(lambda: kill(container))`, and call `unregister()` once the container has exited. Hooks run when the monitor sees a cancel, when the job loses its lease, and on worker shutdown, each at most once. Also check `ctx.cancelled()` between agent turns or steps. After the grace period (`job_cancel_grace_s`, 10 s) the runner cancels the handler task (`asyncio.CancelledError` inside the handler), so keep `finally` blocks short.
- 7.4 figure "not applicable" short-circuit: `raise JobSkipped(rationale)`. The handler session (storyboard, brief) is committed with status `skipped`, and `progress` holds the rationale.
- 7.5 budgets "checked at claim": `@register_claim_gate("video") async def gate(session) -> bool`. It runs inside the claim transaction, before selecting; False leaves every queued job of that type queued for that cycle while other types still start. Register it from the handler module so it loads with `app/jobs/__init__.py`. Checks at enqueue stay in your service, before `enqueue`.
- Resume: the worker passes the last saved checkpoint as `ctx.checkpoint`. A handler resumes by skipping steps already present there. A retry after a transient failure also gets the checkpoint, so whatever was saved before the failure is kept.

### Worker: `app/jobs/runner.py`, `app/services/job_queue.py`

- `Runner(session_factory, worker_id, handlers=HANDLERS, gates=CLAIM_GATES, clock=utcnow, rand=random.random, jobs_dir=settings.jobs_dir)`, with `run(stop: asyncio.Event)`, `tick() -> list[ActiveJob]`, `reap() -> int`, `claim()`, `shutdown()`. Tests drive `tick()` directly.
- `job_queue` (worker-side, lease-guarded, flush only): `claim_next`, `check_lease`, `set_progress`, `save_checkpoint`, `finish -> "done" | "cancelled" | "lost"`, `fail`, `fail_or_retry`, `defer`, `mark_cancelled`, `requeue`, `reap_stale`, `retry_delay(attempt)`, `deferral_run_after(now, reset_at, rand)`, `render_type_limits()`, `PROVIDER_WAIT_MESSAGE = "waiting for provider limit reset"`.

### Events: `app/services/job_events.py`, `app/api/v1/events.py`

- Every status change and progress update is a `job_events` row: `kind` is `status` or `progress`, `status` is the job status after the change, `message`, and `data` (`attempt`, `run_after`, `reason`, `error`, `worker` where relevant). `record_event(session, job, kind, *, message=None, data=None)` inserts the row and, on Postgres, runs `pg_notify('brainbench_jobs', '{"job_id": ..., "event_id": ...}')` in the same transaction.
- `GET /events` (authenticated router, bare path) sends unnamed SSE messages, starting with `retry: 3000`, then a `: keepalive` comment after every 15 s of silence. Per committed job event it sends two messages:
  - `{"type": "jobs.updated", "payload": JobRead}`
  - `{"type": "jobs.event", "payload": JobEventRead}`
- Only job events exist. A section that needs another event type (for example `sources.updated`) should not add a second stream. It should derive what it needs from `jobs.updated` through `jobTypeInvalidations` (below). If a non-job event really is needed, extend `event_stream` in `app/api/v1/events.py`.
- One LISTEN connection per API process (`EventHub`, its own `NullPool` engine), opened with the first subscriber and closed with the last. When the connection drops, each stream ends; the browser `EventSource` reconnects after 3 s and a new connection opens.
- Tests override `events.get_event_hub` and `events.get_event_sessions` (see `tests/api/v1/test_events.py`, `FakeHub`).

### HTTP: `app/api/v1/jobs.py`, `app/models/jobs.py`

- `GET /api/jobs?status=queued&status=running&type=video&limit=50` returns `list[JobRead]`, newest first. `limit` is 1 to 200; an unknown status is a 422.
- `GET /api/jobs/{id}` returns `JobDetail` (a `JobRead` plus `events: list[JobEventRead]`, oldest first, up to 100), or 404 `job-not-found`.
- `POST /api/jobs/{id}/cancel` returns `JobRead`, 404 `job-not-found`, or 409 `job-finished`.
- `JobRead`: `id, type, status, priority, payload, dedupe_key, attempt, max_attempts, run_after, progress, error, error_reason, cancel_requested, created_at, started_at, finished_at, updated_at`. Datetimes are UTC ISO strings.
- `JobEventRead`: `id, job_id, kind, status, message, data, created_at`.
- `JobStatus` and `JobEventKind` are `Literal`s.

### Web

- `src/api/jobs.ts` exports:
  - Types: `JobStatus`, `JOB_STATUSES`, `JobRead`, `JobEventRead`, `JobDetail`, `JobFilters`.
  - `jobKeys`: `all = ['jobs']`, `lists()`, `list(filters)`, `detail(id)`.
  - `isActive(job)`, `listJobs(filters?)`, `getJob(id)`, `cancelJob(id)`.
- `src/events/invalidations.ts`:
  - `'jobs.updated'` invalidates `jobKeys.all` plus `jobTypeInvalidations[job.type]?.(job)`.
  - `'jobs.event'` invalidates nothing, because it always follows a `jobs.updated` for the same job.
  - To refresh your own queries when your jobs change, add one entry per job type you own, for example `jobTypeInvalidations.ingest = (job) => [sourceKeys.all]` or `jobTypeInvalidations.video = (job) => [topicKeys.detail(String(job.payload.topic_id))]`. Put the ids your page needs into the job `payload`.
- Route `/jobs` (`pages/jobs/JobsPage.tsx`, `JobsTable.tsx`, `JobStatusChip.tsx`, `time.ts`): a table with type, status (a `cancelling` label while a cancel is pending), progress (plus `reason: error` for failed jobs), attempt `n / max`, run-after (shown while a queued job is still waiting), created time, and a Cancel button for queued and running jobs. Live updates arrive through the shell's single `EventSource`.
- `tests/support/jobsMock.ts`: `jobsApi.reset(jobs)` and `makeJob(overrides)` follow the `authMock` pattern. Any test that renders a page calling `listJobs` or `cancelJob` must import it.
