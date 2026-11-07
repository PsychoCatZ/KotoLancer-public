# Official multi-architecture indexes resolved 2026-10-16; update by reviewed digest.
FROM node:24-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/src ./src
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
RUN npm run build

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS runtime
ARG KOTO_RELEASE
LABEL org.opencontainers.image.title="KotoLancer" org.opencontainers.image.revision="${KOTO_RELEASE}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements-prod.lock ./
RUN pip install --no-cache-dir --no-deps -r requirements-prod.lock && pip check \
    && groupadd --gid 10001 kotolancer \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin kotolancer \
    && mkdir -p /data/files && chown 10001:10001 /data/files
COPY kotolancer ./kotolancer
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=frontend /build/dist ./frontend/dist
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "kotolancer.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
