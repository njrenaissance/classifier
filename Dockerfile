# syntax=docker/dockerfile:1
#
# One image, two entry points (ADR-0012). The Azure Container Apps job
# `command` override selects the process:
#     python -m walker      scheduled SharePoint delta walk (producer)
#     python -m processor   queue-triggered download → classify → UPSERT (consumer)
#     alembic upgrade head  schema migration, run as a deploy/init step
#
# PostgreSQL state store (ADR-0013) — the driver is psycopg[binary], which ships
# its own libpq, so the image needs no ODBC layer.

# --- builder: resolve and install runtime dependencies with uv ---------------
FROM python:3.13-slim AS builder

# uv is the sanctioned dependency manager; pin it so builds are reproducible.
RUN pip install --no-cache-dir uv==0.11.17

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first, from the lockfile only, so this layer is cached
# across source changes. --no-dev drops the test/lint toolchain; --frozen
# enforces uv.lock; --no-install-project because src/ is run from PYTHONPATH,
# not installed as a package (there is no build backend).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- runtime: slim image carrying only the venv and the application ----------
FROM python:3.13-slim AS runtime

# Non-root runtime user.
RUN useradd --create-home --uid 1000 app

WORKDIR /app

# The resolved virtual environment from the builder stage.
COPY --from=builder /app/.venv /app/.venv

# Application code, schema migrations, and the placeholder category file
# (production content authored in E9 / #42).
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini categories.md ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLASSIFIER__PROCESSOR_CATEGORY_FILE=/app/categories.md

USER app

# No entry point is baked into the contract — the ACA job command override
# selects walker vs processor. A sensible default for a bare `docker run`.
CMD ["python", "-m", "processor"]
