FROM python:3.13.9-slim@sha256:326df678c20c78d465db501563f3492d17c42a4afe33a1f2bf5406a1d56b0e86

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 gitferry

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY schemas ./schemas
RUN python -m pip install --no-cache-dir .

USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "gitferry.api:app", "--host", "0.0.0.0", "--port", "8080"]
