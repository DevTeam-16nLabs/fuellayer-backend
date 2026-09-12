FROM python:3.13.7-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

ARG RELEASE_SHA=unknown
ENV RELEASE_SHA=${RELEASE_SHA}

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY data ./data

RUN uv sync --locked --no-dev --no-editable \
    && useradd --system --uid 10001 --create-home app

USER app

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn fuellayer.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
