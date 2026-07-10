# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARCHIVEDESK_CONTAINER=1 \
    ARCHIVEDESK_HOST=0.0.0.0 \
    ARCHIVEDESK_PORT=8000 \
    ARCHIVEDESK_DATA_DIR=/data \
    ARCHIVEDESK_STATIC_DIR=/app/static \
    ARCHIVEDESK_DEFAULT_OUTPUT_ROOT=/exports

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 archivedesk \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app archivedesk

WORKDIR /app

COPY backend /app/backend
RUN python -m pip install --no-cache-dir /app/backend

COPY --from=frontend-build /build/dist /app/static

RUN mkdir -p /data /exports \
    && chown -R archivedesk:archivedesk /app /data /exports

USER 10001:10001

EXPOSE 8000
VOLUME ["/data", "/exports"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).read()"]

CMD ["archivedesk-backend"]
