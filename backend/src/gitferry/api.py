"""Minimal iteration-1 REST boundary."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gitferry.blueprints import MAX_BLUEPRINT_BYTES, BlueprintValidationError, validate_text
from gitferry.runtime import RuntimeStatus, initialize_runtime
from gitferry.state_migrations import SCHEMA_VERSION


def create_app() -> FastAPI:
    readiness = RuntimeStatus()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        managed = os.environ.get("GITFERRY_RUNTIME_MANAGED", "0") == "1"
        readiness.starting(managed=managed)
        if managed:
            try:
                application.state.runtime = initialize_runtime()
            except Exception as exc:
                readiness.failed(exc)
                raise
            readiness.ready(schema_version=SCHEMA_VERSION)
        else:
            readiness.ready(schema_version=None)
        try:
            yield
        finally:
            readiness.stopping()

    application = FastAPI(
        title="GitFerry API",
        version="0.1.0-dev",
        description="Iteration-1 validation API; no general migration endpoint exists.",
        lifespan=lifespan,
    )

    @application.get("/healthz")
    def health() -> dict[str, object]:
        return {"status": "ok", "iteration": 1}

    @application.get("/readyz")
    def ready() -> JSONResponse:
        return JSONResponse(
            status_code=200 if readiness.is_ready else 503,
            content=readiness.snapshot(),
        )

    application.post("/api/v1/blueprints/validate")(validate_blueprint)
    return application


async def validate_blueprint(request: Request) -> JSONResponse:
    declared_length = request.headers.get("content-length")
    if declared_length is not None and declared_length.isdigit():
        if int(declared_length) > MAX_BLUEPRINT_BYTES:
            return _oversized_response()

    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_BLUEPRINT_BYTES:
            return _oversized_response()
    try:
        content = chunks.decode("utf-8")
        document = validate_text(content)
    except (UnicodeDecodeError, BlueprintValidationError) as exc:
        return JSONResponse(status_code=422, content={"valid": False, "errors": [str(exc)]})
    return JSONResponse(
        status_code=200,
        content={
            "valid": True,
            "apiVersion": document["apiVersion"],
            "kind": document["kind"],
            "errors": [],
        },
    )


def _oversized_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"valid": False, "errors": ["blueprint exceeds the 1 MiB input limit"]},
    )


app = create_app()
