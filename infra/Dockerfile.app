# API, worker, and init image (design D1, D11). Build context: repository root.
# PYTHON_VERSION is the interpreter compatibility knob; 3.14 is the BACKEND_GOOD_PRACTICES.md §6
# fallback while onnxruntime (fastembed) publishes no cp315 wheels.
ARG PYTHON_VERSION=3.14-slim

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

FROM python:${PYTHON_VERSION} AS runtime
COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Layer 1: third-party dependencies from workspace metadata only.
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/providers/pyproject.toml packages/providers/pyproject.toml
COPY packages/agents/pyproject.toml packages/agents/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# Layer 2: workspace members.
COPY packages/ packages/
COPY apps/api/app/ apps/api/app/
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY infra/anatomy.pin infra/schema.rev infra/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package brainbench-api

ENV PATH=/app/.venv/bin:$PATH \
    BRAINBENCH_DATA=/data
WORKDIR /app/apps/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Interpreter compatibility gate: the unit suite inside the runtime image (CI only).
FROM runtime AS test
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages
COPY conftest.py ./
COPY apps/api/tests/ apps/api/tests/
CMD ["pytest", "-q"]
