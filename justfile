# BrainBench task runner. CI calls only these recipes.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Install every workspace member (Python and web).
install:
    uv sync --frozen
    bun install --frozen-lockfile

# API with reload on :8000 and the Bun dev server with HMR on :3000.
dev: install
    trap 'kill 0' EXIT; \
    (cd apps/api && uv run uvicorn app.main:app --reload --port 8000) & \
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

# Build every image of the stack.
build:
    docker compose build

# Image-level checks that need Docker.
smoke:
    @echo "no smoke checks yet"
