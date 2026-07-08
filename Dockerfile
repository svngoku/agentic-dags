# syntax=docker/dockerfile:1
# Runtime image for the agentic-dags template (worker + MCP stub).
#
# The project has no build system (it is run as modules from the repo root),
# so `uv sync --frozen --no-dev` installs dependencies straight from the
# lockfile without needing the source tree — which keeps the dependency
# layer cached until pyproject.toml / uv.lock change.
FROM python:3.12-slim

# uv from the official distroless image, pinned to a minor release.
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Dependency layer — invalidated only when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 2. Application layer — the project is executed as `python -m src....`
#    from /app, so copying src/ is all that is required.
COPY src ./src

# Put the project venv first on PATH so `python` resolves to it.
ENV PATH="/app/.venv/bin:$PATH"

# Run as a non-root user; /app itself stays writable so runtime artifacts
# (sessions.db, .checkpoints/) can be created next to the code.
RUN useradd --create-home --uid 1000 app && chown app /app
USER app

# Default entrypoint: serve the work-order flow as a Prefect deployment.
# docker-compose overrides this for the MCP stub service.
CMD ["python", "-m", "src.serve"]
