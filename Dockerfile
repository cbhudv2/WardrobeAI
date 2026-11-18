FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

FROM python:3.12.10-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12.10-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN groupadd --gid 10001 wardrobe \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin wardrobe \
    && mkdir -p /app /data /backups \
    && chown -R 10001:10001 /app /data /backups

COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv
COPY --chown=10001:10001 app /app/app
COPY --chown=10001:10001 legacy_data /app/legacy_data

WORKDIR /app
USER 10001:10001
EXPOSE 3000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000", "--no-access-log"]

