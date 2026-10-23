# syntax=docker/dockerfile:1
# Image unique : API + admin + player (builds Vite).
FROM node:22-slim AS web
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml tsconfig.base.json ./
COPY apps/admin apps/admin
COPY apps/player apps/player
COPY packages/sdk-ts packages/sdk-ts
COPY modules modules
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
RUN pnpm --filter @vds/admin exec vite build
# Player : build si un script "build" existe, sinon placeholder neutre (aucune info technique).
RUN mkdir -p /out/player \
 && if node -e 'process.exit(require("./apps/player/package.json").scripts?.build?0:1)'; then \
      pnpm --filter @vds/player build && cp -r apps/player/dist/. /out/player/; \
    else \
      printf '<!doctype html><html><head><meta charset="utf-8"><title></title></head><body></body></html>' > /out/player/index.html; \
    fi

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# ffprobe : durée et dimensions des vidéos (module content.video).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/server apps/server
COPY apps/site-agent apps/site-agent
COPY packages/sdk-py packages/sdk-py
COPY modules modules
RUN uv sync --frozen --no-dev --package vds-server
COPY --from=web /repo/apps/admin/dist /app/web/admin
COPY --from=web /out/player /app/web/player
ENV PATH="/app/.venv/bin:$PATH" \
    VDS_ADMIN_DIR=/app/web/admin \
    VDS_PLAYER_DIR=/app/web/player \
    VDS_MODULES_DIR=/app/modules \
    VDS_MEDIA_DIR=/data/media \
    PYTHONUNBUFFERED=1
RUN useradd -r -u 10001 vds && mkdir -p /data/media && chown vds /data/media
USER vds
VOLUME /data/media
EXPOSE 8000
CMD ["sh", "-c", "python -m vds_server.migrate && exec uvicorn vds_server.main:app_factory --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
