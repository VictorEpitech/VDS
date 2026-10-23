# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/server/pyproject.toml apps/server/pyproject.toml
COPY apps/site-agent apps/site-agent
COPY packages/sdk-py packages/sdk-py
RUN uv sync --frozen --no-dev --package vds-agent
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN useradd -r -u 10001 vds
USER vds
# Aucun port entrant : connexion sortante WebSocket TLS uniquement.
CMD ["python", "-m", "vds_agent"]
