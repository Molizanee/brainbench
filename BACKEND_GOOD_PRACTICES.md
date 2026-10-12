# Backend Good Practices

Conventions for `apps/api` (Python 3.15, FastAPI, SQLAlchemy, uv). The goal is a project that scales in four ways: with code size (no refactor wave as features land), with team size (predictable places for every kind of code), with environments (dev, staging, prod differ only by config), and with speed (fast tests, one-command local setup, Docker when needed).

## 1. Folder structure

Balanced: organized enough that every kind of code has one home, flat enough that nothing is hard to find. The repository is a monorepo: Python members (`apps/api`, `packages/providers`, `packages/agents`) form one `uv` workspace with a single `uv.lock`, shared tool configuration, and `.python-version` at the repository root; `apps/api` declares the two packages as workspace dependencies. `just` at the root is the task runner and CI calls only it.

```
apps/api/
  app/
    main.py            # API entrypoint: create app, register routers, nothing else
    worker.py          # Worker entrypoint: claim jobs, dispatch to app/jobs
    api/
      v1/
        profile.py     # one router per resource, HTTP layer only
        sources.py
        ...
    core/
      config.py        # pydantic-settings Settings + module-level instance
      logging.py       # setup_logging()
    db/
      session.py       # engine, session factory, get_session dependency
      schema.py        # SQLAlchemy ORM models (persistence shape)
    models/
      profile.py       # Pydantic request/response contracts (API shape)
      ...
    services/
      profile.py       # business logic per resource, takes a session
      ...
    jobs/
      ingest.py        # one handler per job type, run by the worker
      ...
  tests/
    conftest.py        # in-memory DB, test client, dependency overrides
    api/v1/test_profile.py   # mirrors app/ one-to-one
    services/test_profile.py
  pyproject.toml       # member: name, dependencies (incl. workspace packages), entrypoints
  .env.example         # committed; .env is not
  README.md

(root)
  pyproject.toml       # [tool.uv.workspace], ruff, mypy, pytest config
  uv.lock              # the only lockfile
  .python-version      # 3.15
  justfile
  infra/Dockerfile.app # build context is the repo root
```

Rules:

- `app/` and `tests/` are siblings at the top level. Tests never live inside `app/`.
- `tests/` mirrors `app/` path for path. The test for `app/services/profile.py` is `tests/services/test_profile.py`.
- API versions are directories (`api/v1`, later `api/v2`). Do not version individual files.
- `packages/providers` (registry, vault, OAuth engines, refresher, usage, selection, wire transports, Strands model adapters) and `packages/agents` (Strands agent factories, `@tool` functions, output models, sandboxed figure tools) are separate packages consumed by `app/jobs`, `app/services/tutor.py`, and `app/services/providers.py`; neither imports from `app`. Tools that need data receive callables or a session from the caller rather than importing the app.

## 2. Layers and what goes where

| Layer | Directory | Owns | Must not contain |
| --- | --- | --- | --- |
| HTTP | `app/api/v1` | route declarations, status codes, dependency wiring | queries, business rules, provider calls |
| Contracts | `app/models` | Pydantic request and response schemas, field validation | ORM classes, DB access |
| Business | `app/services` | rules, orchestration, validation that needs data, logging of domain events | FastAPI imports, HTTP types |
| Persistence | `app/db` | engine, sessions, ORM schema, migrations | business rules |
| Jobs | `app/jobs` | long-running work; calls services and `packages/agents` | HTTP types |
| Cross-cutting | `app/core` | config, logging, shared errors | anything resource-specific |

A request flows: router → service (with an injected session) → ORM → service returns a domain result → router maps it to a response model. Routers are thin translators. If a router grows an `if`, that logic belongs in a service.

Request and response contracts are separate models (`ProfileCreate` is not `ProfileRead`). Never return ORM objects from a route.

Services are classes or modules per resource that receive a `Session` (or the dependencies they need) in their constructor. Because they do not know about HTTP, they are testable without a client, and persistence can be swapped (different DB, external API) without touching routers. Call them services or repositories; pick one name per project and keep it.

## 3. Config

One `Settings` class in `app/core/config.py` using `pydantic-settings`, one module-level instance imported everywhere.

- Environment variables fill environment-specific values (`DATABASE_URL`, `BRAINBENCH_DATA`, `BRAINBENCH_PORT`). The compose file sets working defaults for all of them so `docker compose up` needs no `.env`; locally outside Docker they come from `.env`, which is gitignored while `.env.example` is committed with every key and a safe placeholder. Secrets created at runtime (the vault key, the instance password hash) live in the data directory or the database, never in environment variables.
- Non-sensitive constants with sane defaults (`app_name`, concurrency caps, budgets, chunk sizes) live as defaults on `Settings`, not in env files. Env can override them.
- Derived values (for example a DSN assembled from parts) are `@property` methods on `Settings`.
- Nothing reads `os.environ` outside `config.py`.

Switching environments must be boring: same image, different variables.

## 4. Logging

`app/core/logging.py` exposes `setup_logging()` called once from each entrypoint (`main.py`, `worker.py`). Modules get a logger with `logging.getLogger(__name__)`. Start with a plain formatter (time, level, logger, message). Move to JSON output when something consumes the logs; the call sites do not change.

Log at the service and job layer where the domain event happens, with ids (job id, source id, topic id), never secrets, never full prompts at INFO.

## 5. Dependency injection

Use FastAPI `Depends`. The session is injected into the service, the service into the route:

```python
def get_profile_service(session: Session = Depends(get_session)) -> ProfileService:
    return ProfileService(session)

@router.get("/profile", response_model=ProfileRead)
def read_profile(svc: ProfileService = Depends(get_profile_service)):
    return svc.get()
```

Tests override `get_profile_service` (or `get_session`) with `app.dependency_overrides`. Do not introduce a DI container until wiring becomes genuinely cross-cutting; the built-in mechanism covers small and medium services with less boilerplate and one fewer mental model.

The worker has no FastAPI request scope. `worker.py` builds the session and services explicitly per job and passes them to the handler; handlers take dependencies as parameters so they are testable the same way.

## 6. Python 3.15 and lazy imports (PEP 810)

Python version is pinned in `.python-version` and `requires-python = ">=3.15"`, and `infra/Dockerfile.app` takes it from the `PYTHON_VERSION` build argument (default `3.15`). `uv` installs and uses it (`uv python install`, `uv sync`, `uv run`). The compatibility gate for a new interpreter or dependency is not `uv lock`: CI builds the image and runs `python -c "import app.main, app.worker"` plus the unit suite inside it, because a wheel can resolve and still fail to import.

Python 3.15 adds explicit lazy imports: `lazy import x` and `lazy from x import y` bind a proxy immediately and load the module on first use. The API process must not pay for the worker's dependencies, and the worker must not load every job type's dependencies on startup. Lazy imports give that while keeping every import at the top of the file where the dependency graph stays visible.

Rules:

- Imports stay at module top level. Prefer `lazy import` over moving an import inside a function.
- Mark lazy: anything heavy or path-specific. In this project: `strands`, `fastembed`, `fitz` (pymupdf), `trafilatura`, `docker`, `numpy`, `packages.agents.*`, `packages.providers.transports.*`, job handler modules imported by the worker dispatcher, and annotation-only imports (replaces `if TYPE_CHECKING:` blocks for runtime-cheap typing).
- Keep eager: anything whose import has side effects or registers something. `app.core.config`, `app.core.logging`, `app.db.schema` (SQLAlchemy mapper registry must be complete before relationships resolve and before Alembic autogenerate), routers passed to `include_router` in `main.py`, Alembic `env.py`, pytest plugins and `conftest.py`.
- Syntax limits: `lazy` is only legal at module level. It is a `SyntaxError` inside functions, class bodies, `try`/`except`, with `import *`, and with `from __future__`. Optional dependencies guarded by `try/except ImportError` therefore stay eager; use a lazy import plus a check at the use site instead if deferral matters.
- Errors move to first use. An `ImportError` for a lazily imported module surfaces where the name is first touched, not at startup. Every job handler must be exercised by a test, and CI runs the suite twice: once in the default mode and once with `BRAINBENCH_EAGER_IMPORTS=1` (`just test-py-eager`, CI job `python-eager-imports`), which forces every import eager and turns a missing or broken dependency into a startup failure. CPython 3.15.0 accepts only `all` and `normal` for `PYTHON_LAZY_IMPORTS`, so the eager mode is implemented by the root `conftest.py`, which installs a test-only lazy-imports filter that rejects laziness for every import; it is the one exception to the filter rule below.
- Never enable the global `-X lazy_imports=all` or `PYTHON_LAZY_IMPORTS=all`. Laziness is opt-in per line so it can be reasoned about per line. Do not install a `sys.set_lazy_imports_filter`.
- Lazy modules do not appear in `sys.modules` until reified; code that inspects `sys.modules` (plugin discovery, monkeypatching in tests) must reify first by touching the name.
- Fallback if 3.15 wheels are missing for a dependency and the image smoke fails: one change set, applied together. `.python-version` → `3.14`; `requires-python = ">=3.14"`; `PYTHON_VERSION` default and base image → `3.14-slim`; `uv lock` regenerated; the CI matrix switched to 3.14; and every `lazy import a, b` replaced by a module-level `__lazy_modules__ = {"a", "b"}` followed by plain `import a, b` (3.14 cannot parse the `lazy` keyword at all, so this is mandatory, not optional). On 3.15 the two forms are equivalent; on 3.14 the imports are eager. Do not mix both forms in one file, and revert the whole set together when wheels land.

Example `app/jobs/ingest.py` header:

```python
import logging

from app.db.session import Session
from app.services.sources import SourceService

lazy import fitz
lazy import trafilatura
lazy from fastembed import TextEmbedding

log = logging.getLogger(__name__)
```

## 7. Testing

- `tests/conftest.py` builds an isolated database per test session (SQLite in memory for pure logic; a disposable Postgres schema when pgvector or `SKIP LOCKED` semantics are under test), a FastAPI `TestClient`, and the dependency overrides.
- Start with happy paths per resource, then add the edge cases the specs enumerate: validation errors, 404 and 409, limits, state transitions.
- Services get direct tests without the HTTP layer; routers get thin contract tests.
- No external calls in unit tests. Provider APIs, Docker, and the network are replaced by fixtures that replay recorded exchanges. Integration tests that touch real providers or containers live under `tests/integration/` and run in a separate CI job.
- `pyproject.toml` sets `[tool.pytest.ini_options] pythonpath = ["."]` so tests import `app.services.profile` exactly as application code does.
- Tests must be deterministic and independent of ordering; no shared mutable module state.

## 8. Tooling

- `uv` for everything, from the repository root: `uv sync` installs every workspace member, `uv run --package brainbench-api pytest` tests one member, `uv run ruff check .` lints, `uv run mypy apps packages` typechecks. The root `uv.lock` is the only lockfile and is committed; Docker uses `uv sync --frozen`.
- The root `pyproject.toml` holds the workspace definition and ruff, mypy, and pytest config; each member's `pyproject.toml` holds its own name, dependencies (workspace packages via `[tool.uv.sources]`), `requires-python`, and entrypoints. No `requirements.txt`.
- `ruff` (lint and format) and `mypy` run in CI and pre-commit.

## 9. Docker and local orchestration

- `infra/Dockerfile.app`: `python:${PYTHON_VERSION}-slim` base, install `uv`, build context is the repository root. Two sync layers so dependency installation caches independently of source: first copy the root `pyproject.toml`, `uv.lock`, and every member's `pyproject.toml` and run `uv sync --frozen --no-dev --no-install-workspace` (third-party dependencies only; a plain sync here fails because the workspace members' source does not exist yet); then copy `apps/api/app/` and `packages/`, and run `uv sync --frozen --no-dev --package brainbench-api` to install the workspace members. Set `PATH` to the venv, expose the port, run `uvicorn app.main:app`. The same image runs `init` (`alembic upgrade head` and bootstrap) and the worker (`python -m app.worker`).
- The root `docker-compose.yml` runs the same image locally with the same environment variable names used in production. Local and deployed environments differ only by values.
- Add `--build-essential` style packages only when a dependency actually compiles; prefer wheels.

## 10. Adding a feature

1. Add or change the ORM schema in `app/db/schema.py` and write an Alembic migration.
2. Add request and response models in `app/models/<resource>.py`.
3. Implement the rule in `app/services/<resource>.py`.
4. Expose it in `app/api/v1/<resource>.py` and register the router in `main.py` if new.
5. Long-running? Add `app/jobs/<type>.py` and register the type in the worker dispatcher with a lazy import.
6. Mirror each file with a test under `tests/`.

If a change touches files outside this list, the boundaries are being bent; stop and move the logic to its layer.
