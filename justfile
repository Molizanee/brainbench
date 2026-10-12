# BrainBench task runner. CI calls only these recipes.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Install every workspace member (Python and web).
install:
    uv sync --frozen
    bun install --frozen-lockfile

dev_db := "postgresql+asyncpg://brainbench:brainbench@127.0.0.1:54329/brainbench"

# Postgres in Docker, then the API with reload on :8000 and the Bun dev server with HMR on :3000.
dev: install
    docker compose -f docker-compose.yml -f infra/compose.dev.yml up -d --wait db
    cd apps/api && DATABASE_URL={{dev_db}} uv run alembic upgrade head
    mkdir -p data
    trap 'kill 0' EXIT; \
    (cd apps/api && DATABASE_URL={{dev_db}} BRAINBENCH_DATA="$PWD/../../data" uv run uvicorn app.main:app --reload --port 8000) & \
    (cd apps/web && API_URL=http://localhost:8000 bun run dev) & \
    wait

# Lint and typecheck everything.
lint: lint-py lint-web

lint-py:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy apps packages conftest.py

lint-web:
    bun run --cwd apps/web lint
    bun run --cwd apps/web typecheck

# Run every unit suite.
test: test-py test-web

test-py:
    uv run pytest

# Same suite with every lazy import forced eager (see conftest.py).
test-py-eager:
    BRAINBENCH_EAGER_IMPORTS=1 uv run pytest

test-web:
    bun run --cwd apps/web test

# Build every image of the stack (app, web, and both sandboxes).
build:
    docker compose build

python_image := env("PYTHON_VERSION", "3.14-slim")

# Image-level checks that need Docker.
smoke: image-smoke smoke-manim

# Interpreter compatibility gate: build the app image, import both entrypoints, run the unit
# suite inside it (default and eager imports).
image-smoke:
    docker build -f infra/Dockerfile.app --target runtime --build-arg PYTHON_VERSION={{python_image}} -t brainbench-app:smoke .
    docker run --rm brainbench-app:smoke python -c "import app.main, app.worker"
    docker build -f infra/Dockerfile.app --target test --build-arg PYTHON_VERSION={{python_image}} -t brainbench-app:test .
    docker run --rm brainbench-app:test pytest -q
    docker run --rm -e BRAINBENCH_EAGER_IMPORTS=1 brainbench-app:test pytest -q

# Against DATABASE_URL: upgrade, no drift between ORM and migrations, full downgrade, upgrade.
migrations-check: check-schema-rev
    cd apps/api && uv run alembic upgrade head
    cd apps/api && uv run alembic check
    cd apps/api && uv run alembic downgrade base
    cd apps/api && uv run alembic upgrade head

# infra/schema.rev must name the single Alembic head.
check-schema-rev:
    test "$(cd apps/api && uv run alembic heads | awk '{print $1}')" = "$(cat infra/schema.rev)"

# Build the Manim sandbox and render the two-section fixture with the sandbox flags.
smoke-manim:
    docker build -f infra/Dockerfile.manim -t brainbench/manim .
    infra/smoke/manim_check.sh brainbench/manim

# Every Postgres-backed integration test against DATABASE_URL (migrated first). CI runs this.
integration:
    test -n "${DATABASE_URL:-}" || { echo "set DATABASE_URL to a Postgres database" >&2; exit 1; }
    cd apps/api && uv run alembic upgrade head
    uv run pytest -m integration apps/api/tests/integration -q

# Section 2: job queue, LISTEN/NOTIFY and runner tests against the Postgres in DATABASE_URL.
jobs-integration:
    test -n "${DATABASE_URL:-}" || { echo "set DATABASE_URL to a Postgres database" >&2; exit 1; }
    uv run pytest -m integration apps/api/tests/integration/jobs

# Section 3: no plaintext token in Postgres rows, API responses, or logs after connected accounts.
providers-integration:
    cd apps/api && uv run alembic upgrade head
    uv run pytest -m integration apps/api/tests/integration -q
