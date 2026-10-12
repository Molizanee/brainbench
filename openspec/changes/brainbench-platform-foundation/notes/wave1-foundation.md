# Wave 1 notes: foundation (tasks 1.1 to 1.6)

Branch `wave1-foundation`. Tasks 1.1-1.6 are checked in `tasks.md`. Commits, in order:

| Commit | Content |
| --- | --- |
| `feat(foundation): monorepo ... (1.1)` | uv workspace, Bun workspace, justfile, CI, `apps/api` skeleton, job registry |
| `feat(foundation): Bun web app ... (1.2)` | `apps/web`, `server.ts` proxy, API client, event hook, shell |
| `feat(foundation): Alembic and initial migration ... (1.5, part 1)` | `app/db/schema.py`, migration `0001`, schema guard |
| `feat(foundation): apply the Python 3.14 fallback set ...` | the BACKEND_GOOD_PRACTICES.md §6 change set, isolated so it can be reverted |
| `feat(foundation): Manim sandbox image ... (1.4)` | `infra/Dockerfile.manim`, render check |
| `feat(foundation): app, web and anatomy images ... (1.3)` | Dockerfiles, `docker-compose.yml`, `init` bootstrap, worker probe |
| `feat(foundation): CI interpreter gate ... (1.5)` | image gate and migration checks |
| `feat(foundation): instance password ... (1.6)` | wizard, login, session cookie, 401 handling |

## Python version actually used: 3.14 (fallback applied)

Python 3.15 failed the compatibility gate, so the complete §6 fallback set is applied as one commit:

- `fastembed` is a section 1 dependency (the `init` service pre-downloads the embedding model) and depends on `onnxruntime`, which publishes wheels only for `cp311`-`cp314t`. With `requires-python >=3.15`, `uv lock` is unsatisfiable. Building `infra/Dockerfile.app` with `PYTHON_VERSION=3.15-rc-slim` fails at `uv sync`: "onnxruntime (v1.31.0) only has wheels with the following Python ABI tags: cp314, cp314t".
- Separately, `python:3.15-slim` does not exist on Docker Hub yet (only `3.15-rc-*` tags as of 2026-10-11), so the task's `3.15-slim` default could not build in any case.
- Applied: `.python-version` 3.14; `requires-python = ">=3.14"` in all three members; ruff `py314` and mypy `3.14`; `uv.lock` regenerated; CI matrix on 3.14; every `lazy import` rewritten as `__lazy_modules__ = {...}` followed by a plain import. `infra/Dockerfile.app` defaults to `PYTHON_VERSION=3.14-slim` (it was created after the fallback commit, so it also has to be set back to `3.15-slim` when reverting).
- Before the fallback, the whole stack except `fastembed` (FastAPI, pydantic-core, SQLAlchemy and greenlet, asyncpg, argon2-cffi, docker, alembic) installed from cp315 wheels, and ruff 0.17 and mypy 2.4 parse `lazy import`.
- To revert when cp315 `onnxruntime` wheels exist: revert the fallback commit, set `PYTHON_VERSION` back in `infra/Dockerfile.app`, rewrite the `__lazy_modules__` blocks written later (see below) back to `lazy import`, run `uv lock`, and run `just image-smoke` with `PYTHON_VERSION=3.15-slim`.

### `PYTHON_LAZY_IMPORTS=none` does not exist in CPython 3.15.0

The released 3.15.0 accepts only `all` and `normal` for `PYTHON_LAZY_IMPORTS` and `-X lazy_imports`. `none` aborts the interpreter at startup ("invalid value; expected 'all' or 'normal'"). The eager-imports CI job (`python-eager-imports`, `just test-py-eager`) therefore sets `BRAINBENCH_EAGER_IMPORTS=1`, and the root `conftest.py` then installs `sys.set_lazy_imports_filter(lambda *a: False)`. Before the fallback I verified on 3.15 that this filter turns a broken `lazy import` into an import-time `ModuleNotFoundError`. It is the only filter in the repo and it is test-only. On 3.14 it is a no-op because every import is already eager. BACKEND_GOOD_PRACTICES.md §6 still says `PYTHON_LAZY_IMPORTS=none` and needs that correction.

### Lazy-import form to use while on 3.14

In any file that defers a heavy import, write the block after the eager imports, without a `noqa` (ruff accepts it):

```python
__lazy_modules__ = {"fitz", "trafilatura", "fastembed"}
import fitz
import trafilatura
from fastembed import TextEmbedding
```

Do not mix this with `lazy import` in the same file. The current uses are in `app/jobs/registry.py`, `app/db/revision.py`, `app/bootstrap.py`, `app/worker.py` and `app/services/auth.py`.

## Commands verified (locally, macOS arm64, Docker Desktop 29.2.1, uv 0.13.0, Bun 1.3.13)

- `just lint`: ruff check, ruff format --check, mypy strict on `apps packages conftest.py`, oxlint, prettier --check, `tsc --noEmit` (TypeScript 7.0.2). All green.
- `just test`: 60 Python tests, 25 web tests. `just test-py-eager`: 60 passed.
- `bun test` in `apps/web`: 25 pass, including `tests/server.test.ts`, which asserts that `/api/health` and `/events` go through the proxy and never return `text/html`. Three consecutive runs, no flakes.
- `just image-smoke`: builds `infra/Dockerfile.app`, runs `python -c "import app.main, app.worker"` in the runtime image (3.14.8), then 60 tests inside the test target, normally and with eager imports.
- `just migrations-check` against pgvector pg16: `schema.rev` equals the head, upgrade, `alembic check` ("No new upgrade operations detected"), `downgrade base` (it also drops the `vector` extension), upgrade.
- `just smoke-manim`: renders the two-section fixture with `--network none --cpus 2 --memory 4g --pids-limit 256 --read-only --tmpfs /tmp`, `/work:ro` and `/out` writable. Asserts `/out/videos/scene/720p30/lesson.mp4` and `/out/videos/scene/720p30/sections/lesson.json` with sections `["intro", "transform"]`, each with its clip (`lesson_0000_intro.mp4`, `lesson_0001_transform.mp4`), then runs `ffprobe` in the image.
- `docker compose up --build` with no `BRAINBENCH_*` variables, from no `./data` and no volumes, on the default port 3000 (it was free): `BrainBench ready at http://localhost:3000`; `init` exited 0; api healthy; worker log "sandbox probe passed (host data dir .../wave1-foundation/data)"; `./data/vault.key` is `-rw-------`; `./data/skills/anatomy/.commit` equals `infra/anatomy.pin`; `brainbench/manim` and `brainbench/anatomy` are on the daemon; `GET /` returns 200 `text/html`. Headless Chromium (from the anatomy image) rendered the wizard at `/`: "Welcome", two password fields, "Set password and continue".
- A second `docker compose up`: init logs "schema already at head 0001; no migration ran", "vault key ok", "anatomy skill unchanged", "embedding model ... already present", and exits 0. The key hash is unchanged.
- Auth through the proxy: `/api/auth/me` returned `{password_set:false}`, `POST /api/auth/setup` returned 201 with `HttpOnly; SameSite=lax` `brainbench_session`, `me` then returned authenticated, a wrong login returned 401 `invalid-password`, and `settings.password_hash` starts with `$argon2id$`.
- Afterwards: `docker compose down -v`, `./data` removed, scratch containers and images removed, `git status` clean.

## Contracts for sections 2 to 9 (final signatures)

### API routers: `apps/api/app/api/v1/__init__.py`

```python
public_router = APIRouter()          # health + auth, no session needed
router = APIRouter(dependencies=[Depends(require_session)])   # authenticated API
```

`app/main.py` includes both once. Each section adds `app/api/v1/<name>.py` with a router that declares its own full prefix, then adds one line in `__init__.py`: `router.include_router(<name>.router)`.

- URL paths are `/api/...`, not `/api/v1/...`: `APIRouter(prefix="/api/profile", tags=["profile"])`. The Bun server proxies only `/api/*` and `/events`.
- Section 2's SSE endpoint is a bare `@router.get("/events")` in its own module, registered on the same `router`, so it is authenticated by the cookie. Emit unnamed SSE messages whose `data` is JSON `{"type": "<prefix>.<event>", "payload": ...}`; that is the shape the web hook parses.
- Errors: raise `app.core.errors.NotFound | Conflict | Unauthorized | DomainError(message, code=...)` from services. `app/api/errors.py` maps them to 404/409/401/400 with body `{"detail": {"code": "...", "message": "..."}}`. The web client turns that into `ApiError{status, code, message}`, and a 422 becomes `ApiError.fields` keyed by the field path (`skills.0.name`). Subclass these with a class-level `code`, as `app/services/auth.py` does.
- ruff allows `param = Depends(...)` defaults (bugbear `extend-immutable-calls`).

### Auth: `apps/api/app/api/deps.py`, `app/services/auth.py`

- `get_auth_service(session) -> AuthService`, `session_token(request) -> str | None`, `async require_session(...) -> None` (raises 401 `{"code": "unauthenticated"}`).
- Endpoints: `GET /api/auth/me -> {password_set, authenticated}` (always 200), `POST /api/auth/setup {password}` (201, 409 `password-already-set`, 422 when shorter than 8), `POST /api/auth/login {password}` (200, 401 `invalid-password`, 409 `setup-required`), `POST /api/auth/logout` (204).
- Cookie `brainbench_session`: HttpOnly, SameSite=Lax, 30 days, `Secure` only when `BRAINBENCH_PUBLIC_URL` is https. It is signed with `settings.session_secret`, which bootstrap generates into the DB. A new password hash invalidates old sessions.

### Jobs: `apps/api/app/jobs/registry.py`, `app/jobs/errors.py`

```python
@dataclass
class JobContext:
    job_id: UUID
    type: str
    payload: dict[str, Any]
    checkpoint: dict[str, Any]
    session: AsyncSession
    save_checkpoint_fn: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    progress_fn: Callable[[str], Awaitable[None]] | None = None
    is_cancelled: Callable[[], bool] = lambda: False
    async def save_checkpoint(self, checkpoint: dict[str, Any]) -> None  # updates ctx.checkpoint, then calls save_checkpoint_fn
    async def progress(self, message: str) -> None                       # calls progress_fn
    def cancelled(self) -> bool                                          # calls is_cancelled

type Handler = Callable[[JobContext], Awaitable[None]]
HANDLERS: dict[str, Handler]
def register(job_type: str) -> Callable[[Handler], Handler]   # ValueError on a duplicate type

class PermanentJobError(Exception):  __init__(message, *, reason="permanent"); .reason
class ProviderLimitReached(Exception): __init__(reset_at: datetime | None = None, message=None); .reset_at
```

- Section 2's worker loop fills the three `*_fn` fields when it builds a `JobContext`. Handlers only call the methods.
- Registering a handler: create `app/jobs/<type>.py` with `@register("<type>") async def run(ctx)`, then add one eager line to `app/jobs/__init__.py`: `from app.jobs import <type> as <type>`. Registration is an import side effect, so the handler module itself is eager. Its heavy dependencies go behind `__lazy_modules__`. `app/jobs/noop.py` (type `noop`) is the example and the diagnostic job.
- `app/worker.py` startup order: `prepare_sandboxes()` (host path, probe), then `run()` (schema guard, log handlers, placeholder idle loop that sleeps `settings.worker_idle_sleep_s`). Section 2 replaces the loop body in `run()`. `SchemaMismatch`, `HostPathError` and `SandboxProbeError` exit with code 1 and a message.

### Config: `apps/api/app/core/config.py`

`settings = Settings()` uses pydantic-settings, case-insensitive env, `.env` file, and `env_ignore_empty=True` (compose passes `${X:-}` as an empty string, which means unset). Fields (env name in upper case):

- `brainbench_data: Path = /data`, `database_url: str` (asyncpg), `brainbench_oauth_callbacks: bool = False` (compose sets 1), `brainbench_port: int = 3000`, `brainbench_bind = "127.0.0.1"`, `brainbench_public_url: str | None`, `brainbench_data_host: Path | None`.
- Constants: `session_cookie_name`, `session_max_age_s`, `anatomy_repo_url`, `anatomy_pin_file = /app/infra/anatomy.pin`, `embedding_model = "BAAI/bge-small-en-v1.5"`, `embedding_dim = 384`, `sandbox_probe_image = "brainbench/manim"`, `worker_idle_sleep_s`, `job_concurrency = 2`, `render_concurrency = 1`.
- Properties: `jobs_dir`, `assets_dir`, `models_dir`, `skills_dir`, `vault_key_path`, `public_url`.

### DB sessions: `apps/api/app/db/session.py`

```python
engine: AsyncEngine; SessionFactory: async_sessionmaker[AsyncSession]
async def get_session() -> AsyncIterator[AsyncSession]           # FastAPI dependency; services commit explicitly
@asynccontextmanager async def session_scope() -> AsyncIterator[AsyncSession]  # worker: commit on success, rollback on error
```

### Schema: `apps/api/app/db/schema.py` (the single ORM module), migration `0001`

- All D8 tables exist, including `tutor_threads` and `tutor_messages`, so task 9.1 does not need to create them, only to add columns if it needs any. ORM class names: `InstanceSettings` (table `settings`, singleton `id = 1`), `Profile`, `CurrentSkill`, `TargetSkill`, `Job`, `JobEvent`, `UsageCounter`, `Source`, `Chunk`, `CurationRun`, `Topic`, `TopicRevision`, `TopicChunk`, `StudyPlan`, `PlanItem`, `Storyboard`, `Asset`, `StudySession`, `ProviderCredential`, `ProviderBlock`, `ProviderUsageWindow`, `ProviderUsage`, `TutorThread`, `TutorMessage`.
- Value sets from the specs are module constants used by the CHECK constraints: `SKILL_LEVELS`, `TARGET_LEVELS`, `PRIORITIES`, `DEPTHS`, `SOURCE_TYPES`, `SOURCE_STATUSES`, `JOB_STATUSES`, `ASSET_FORMATS`, `REVISION_ORIGINS`, `MASTERY_RATINGS`, `TUTOR_CONTEXTS`, `TUTOR_ROLES`. Reuse them in `app/models`.
- Columns beyond the D8 list that later sections can rely on:
  - `jobs`: `priority`, `dedupe_key` (unique while status is queued or running, for 7.5 dedupe), `cancel_requested`, `error_reason`, `started_at`, `finished_at`.
  - `sources`: `uri`, `note`, `file_path`, `error`, `chunk_count`, `job_id`.
  - `chunks.location` is JSONB (`{"page": n}` / `{"path": ...}` / `{"fragment": ...}`).
  - `topics.orphaned_at`.
  - `topic_revisions`: `embedding vector(384)` (for reconcile) and `curation_run_id`.
  - `plan_items`: `excluded`, `relevance`, `rationale`, `serves_targets`, `estimate_minutes`. `week_start` and `week_span` are nullable for excluded items.
  - `assets`: `previous_asset_id`, `is_current`, `feedback`, `script`, `files` JSONB, `provenance` JSONB, `duration_s`.
  - `settings`: `password_hash`, `session_secret`, `vault_fingerprint`, `enabled_providers` (JSONB, `NULL` = all registry providers enabled), `provider_routing`, `render_minutes_month = 600`, `generation_jobs_day = 20`.
  - `ProviderCredential.__repr__` never includes token columns.
- JSON columns use `Json = JSON().with_variant(JSONB(), "postgresql")` so unit tests run on SQLite. Migrations use `postgresql.JSONB()` directly.
- Adding columns or tables: edit `schema.py`, write a new revision in `app/db/migrations/versions/` (`cd apps/api && uv run alembic revision --autogenerate --rev-id 0002 -m "..."` against a migrated Postgres), hand-check the `downgrade()`, update `infra/schema.rev` to the new head, and run `just migrations-check`. Parallel sections will each branch from `0001`. Whoever merges must re-chain `down_revision` so there is a single head; `just check-schema-rev` fails while there are two heads.
- `app/db/revision.py`: `head_revision()`, `known_revisions()`, `async current_revision(conn)`, and `async ensure_schema_at_head(engine, component)`, which raises `SchemaMismatch`. The API runs it in its lifespan, the worker in `run()`. Tests build the app with `create_app(schema_guard=False)`.

### Bootstrap and host-side services (section 1 owns them; reuse them)

- `app/bootstrap.py` (`python -m app.bootstrap`, the compose `init` command): steps 1-3 failing exits 1; skill and model failures are logged and the instance still starts. The embedding model goes to `/data/models` with fastembed's cache layout. Section 5.3 should call `TextEmbedding(model_name=settings.embedding_model, cache_dir=str(settings.models_dir))`. Marker `/data/models/.ready-BAAI--bge-small-en-v1.5`.
- `app/services/vault_key.py`: `KEY_BYTES = 32` (raw bytes, for AES-256-GCM), `fingerprint(key) -> sha256 hex`, `write_new_key(path)` (0600, never overwrites), `key_state(path, recorded) -> VaultKeyState`, `async ensure_vault_key(session, path)`. `VaultKeyState.locked` covers `locked-missing`, `locked-mismatch` and `locked-invalid`. Section 3.2 should use `key_state` for the "locked" status and `write_new_key` plus the fingerprint update for `POST /providers/vault/reset`, after deleting the old file and the credentials.
- `app/services/instance_settings.py`: `async load_instance_settings(session) -> InstanceSettings` gets or creates the singleton and flushes. The caller commits.
- `app/services/host_paths.py`: `resolve_host_data_dir(client, data_dir, override) -> Path` (`BRAINBENCH_DATA_HOST` wins when set, otherwise self-inspection of `Mounts[].Source`), `to_host(path, data_dir, host_data_dir) -> Path` (rejects paths outside the data dir), `run_sandbox_probe(client, image, data_dir, host_data_dir)`. `client` is a `docker.DockerClient`. Section 7.2 should build `-v` sources with `to_host` and restrict mounts to `jobs/<id>/work`, `jobs/<id>/out` and `skills/anatomy`. `prepare_sandboxes()` in `app/worker.py` returns the resolved host path; keep it for the runner.
- `app/services/skill_install.py`: `read_pin`, `installed_commit`, and `install_pinned_skill(target, pin, repo_url)`, which recovers an interrupted swap. The vendored skill lives in `/data/skills/anatomy` with no `.git`.

### Sandbox images

- `brainbench/manim`: Manim Community v0.22.0, pinned by digest, Python 3.14 in the image, user `manimuser` (uid 1000). It adds `ffmpeg` and `ffprobe` (the base image ships neither; Manim uses PyAV). `HOME` and the caches are under `/tmp`, so `--read-only --tmpfs /tmp` works. `sections/lesson.json` entries have `name`, `type`, `video` (file name relative to `sections/`), `duration` (string), `nb_frames`, `width`, `height`, `codec_name`. Its `duration` is already there, but 7.3 still uses `ffprobe` as specified.
- `brainbench/anatomy`: `node:20-bookworm-slim` pinned by digest (Node 20.20.2), Debian `chromium` at `CHROME_PATH=/usr/bin/chromium`, user `node`, workdir `/workspace`, no npm packages. Node 20 is past upstream EOL; the spec names it, so a move to 22 is a spec change. Chromium in a container needs `--no-sandbox` (I used `--headless=new --no-sandbox --disable-gpu`).
- `infra/anatomy.pin` is `689dcf966e63d92be10a5d593c6e19e10acd4422` (wheresryan22/anatomy `main` on 2026-10-11).

### Web: `apps/web`

- `src/api/client.ts`: `apiGet<T>(path, opts?)`, `apiPost<T>(path, body?, opts?)`, `apiPatch<T>(path, body?, opts?)`, `apiDelete<T = void>(path, opts?)`. `body` may be a `FormData` (sent as is, for the PDF upload in 5.5). `opts`: `{signal?, headers?, redirectOn401?: boolean}`. `ApiError {status, code, message, fields?}`, `toApiError(...)`, `configureApiClient({baseUrl?, onUnauthorized?})`. `main.tsx` installs the 401 handler (clear cache, go to `/login`). Pass `redirectOn401: false` where a 401 is an expected answer.
- `src/routes.tsx` is the single route table. To add a section, append one entry to `sections`: `{ path: 'profile', label: 'Profile', icon: PersonIcon, Page: lazy(() => import('./pages/profile/ProfilePage')) }`. Pages are default exports under `src/pages/<section>/`. The app shell builds its navigation from `sections`, and every section route sits behind `RequireSession`. Nested routes inside a section (for example `topics/:id`) need a route entry next to it; extend `SectionRoute` if that comes up. `pages/providers/ProvidersPage.tsx` is a placeholder for 3.8 to replace; the wizard lands on `/providers`.
- `src/events/useEventSource.ts`: `type AppEvent = {type: string; payload: unknown}`, `type EventInvalidations = Record<string, (payload: unknown) => QueryKey[]>`, `parseEvent(data)`, `keysForEvent(event, map)`, `useEventSource(invalidations = {}, url = '/events')`. It is mounted once in `AppShell` with `src/events/invalidations.ts` (empty). The default rule: event type `jobs.updated` invalidates every query under `['jobs']`, so name query keys by the event prefix, or add one explicit entry in `invalidations.ts`. Until section 2 serves `/events`, the API answers 404 and the `EventSource` closes without retrying.
- `src/api/auth.ts` (`authKeys`, `getAuthState`, `setupPassword`, `login`, `logout`) is the pattern for resource modules: types named after the FastAPI models, plus query-key factories.
- Tests: `tests/harness.tsx` has `renderRoutes(routes, path)` and `renderElement(el)` (theme, fresh `QueryClient`, memory router). `tests/setup.ts` registers happy-dom but keeps Bun's `fetch`, `Request`, `Response`, `Headers`, `FormData` and `AbortController`, so tests can run real `Bun.serve` upstreams; it also runs Testing Library `cleanup` after each test. Bun shares `mock.module` across files, so any test that renders routed pages must import `tests/support/authMock.ts` and call `authApi.reset(...)` (copy that pattern for other API modules).
- Bun 1.3 resolves bundled HTML assets relative to the working directory: run the build from `dist/` (`bun run start` does `cd dist && bun server.js`, and `Dockerfile.web` uses `WORKDIR /app/dist`). `bun dist/server.js` from the app root fails with "Bundled file ... not found".
- Toolchain resolved to React 19.3, MUI 9.5, React Router 8.4 (`react-router` package, library mode, `createBrowserRouter`), TanStack Query 5.104, react-hook-form 7.90, zod 4.6, TypeScript 7.0.2, oxlint, prettier (no semicolons, single quotes, width 100).

### Tooling and CI

- `just` recipes: `install`, `dev` (Postgres from `infra/compose.dev.yml` on `127.0.0.1:54329`, migrations, then uvicorn `--reload` on :8000 and `bun --hot` on :3000), `lint` (`lint-py`, `lint-web`), `test` (`test-py`, `test-web`), `test-py-eager`, `build`, `smoke` (`image-smoke`, `smoke-manim`), `migrations-check` (needs `DATABASE_URL`), `check-schema-rev`.
- `.github/workflows/ci.yml` jobs: `python`, `python-eager-imports`, `migrations` (pgvector service), `app-image` (matrix `PYTHON_VERSION`), `manim-sandbox`, `web`. CI has not run on GitHub yet because nothing was pushed. The action major versions (`actions/checkout@v5`, `astral-sh/setup-uv@v6`, `oven-sh/setup-bun@v2`, `extractions/setup-just@v2`) are assumptions to confirm on the first push.
- Python tests: `apps/api/tests/conftest.py` provides `engine` (in-memory SQLite with the full schema), `session_factory`, `session`, `app` (with `get_session` overridden) and `client`. pytest runs with `--import-mode=importlib` and `asyncio_mode = "auto"`. pgvector and `SKIP LOCKED` behavior needs Postgres: put those tests under `tests/integration/`, mark them `@pytest.mark.integration`, and add a CI job in the style of `migrations`.
- `ruff format` also formats Python blocks inside Markdown, so `*.md` and `openspec/` are excluded in `pyproject.toml`.
- The compose project name is the directory name, so worktrees do not collide, but `brainbench/manim` and `brainbench/anatomy` are shared names on the daemon (D11 requires fixed names). App and web images are unnamed and built per project. Host ports 3000, 54545 and 1455 conflict if two worktrees run the stack at once; use `BRAINBENCH_PORT` for the web port. The OAuth ports are fixed by the providers.
- The app containers run as root (the worker needs the Docker socket), so on a Linux host `./data` is root-owned. On Docker Desktop for macOS it is owned by the user.
