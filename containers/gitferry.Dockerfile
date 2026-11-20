FROM node:24.14.0-bookworm-slim@sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8 AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend ./
RUN npm run build

FROM python:3.13.9-slim@sha256:326df678c20c78d465db501563f3492d17c42a4afe33a1f2bf5406a1d56b0e86

ARG GITFERRY_VERSION=0.1.0.dev0
ARG GITFERRY_SOURCE_REVISION=unknown

LABEL org.opencontainers.image.title="GitFerry" \
    org.opencontainers.image.version="$GITFERRY_VERSION" \
    org.opencontainers.image.revision="$GITFERRY_SOURCE_REVISION" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.source="https://github.com/hackepeter87/GitFerry"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GITFERRY_RUNTIME_MANAGED=1 \
    GITFERRY_STATE_DB=/var/lib/gitferry/state/gitferry.sqlite3 \
    GITFERRY_PACKAGE_DIR=/var/lib/gitferry/packages \
    GITFERRY_WORK_DIR=/var/lib/gitferry/work \
    GITFERRY_FRONTEND_DIR=/app/frontend-dist

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 gitferry \
    && install -d -o gitferry -g gitferry -m 0700 \
        /var/lib/gitferry/state /var/lib/gitferry/packages /var/lib/gitferry/work

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY schemas ./schemas
COPY --from=frontend-build /build/frontend/dist ./frontend-dist
RUN python -m pip install --no-cache-dir .

USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
    CMD ["python", "-m", "gitferry.healthcheck"]
CMD ["python", "-m", "gitferry.server"]
