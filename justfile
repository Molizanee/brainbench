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

# Image-level checks that need Docker.
smoke: smoke-manim

# Build the Manim sandbox and render the two-section fixture with the sandbox flags.
smoke-manim:
    docker build -f infra/Dockerfile.manim -t brainbench/manim .
    infra/smoke/manim_check.sh brainbench/manim
