"""Minimal iteration-1 REST boundary."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from gitferry.auth import AuthService
from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    auth_error_response,
    create_auth_router,
)
from gitferry.blueprints import MAX_BLUEPRINT_BYTES, BlueprintValidationError, validate_text
from gitferry.connection_profiles import ProfileConflict, ProfileValidationError
from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.package_api import create_package_admission_router
from gitferry.plan_api import create_plan_router
from gitferry.plan_runner import BackgroundPlanWorker, PlanRunner
from gitferry.profile_api import create_profile_router, profile_error
from gitferry.runtime import RuntimeStatus, initialize_runtime
from gitferry.state_migrations import SCHEMA_VERSION
from gitferry.transfer_plans import PlanService

# Uvicorn configures ``uvicorn.error`` for production output.  A child logger
# keeps the allowlisted request record visible while the raw access logger is
# disabled by the GitFerry server entrypoint.
HTTP_LOGGER = logging.getLogger("uvicorn.error.gitferry.http")


def create_app(
    *,
    auth_service: AuthService | None = None,
    session_cookie_secure: bool = True,
    frontend_dir: Path | None = None,
) -> FastAPI:
    readiness = RuntimeStatus()
    cookie_settings = AuthCookieSettings(secure=session_cookie_secure)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        managed = os.environ.get("GITFERRY_RUNTIME_MANAGED", "0") == "1"
        readiness.starting(managed=managed)
        if managed:
            try:
                application.state.runtime = initialize_runtime()
                if auth_service is None:
                    application.state.auth = AuthService(application.state.runtime.store)
                service = PlanService(application.state.runtime.store)
                runner = PlanRunner(
                    service,
                    lambda _plan: GiteaPlanAdapter(
                        application.state.runtime.store, application.state.runtime.paths
                    ),
                )
                application.state.plan_worker = BackgroundPlanWorker(runner)
                application.state.plan_worker.start()
            except Exception as exc:
                readiness.failed(exc)
                raise
            readiness.ready(schema_version=SCHEMA_VERSION)
        else:
            readiness.ready(schema_version=None)
        try:
            yield
        finally:
            worker = getattr(application.state, "plan_worker", None)
            if worker is not None:
                worker.stop()
            readiness.stopping()

    application = FastAPI(
        title="GitFerry API",
        version="0.1.0-dev",
        description="Iteration-1 validation API; no general migration endpoint exists.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    if auth_service is not None:
        application.state.auth = auth_service

    @application.exception_handler(AuthBoundaryError)
    async def auth_error_handler(_: Request, exc: AuthBoundaryError) -> JSONResponse:
        return auth_error_response(exc)

    @application.exception_handler(ProfileConflict)
    @application.exception_handler(ProfileValidationError)
    async def profile_error_handler(
        _: Request, exc: ProfileConflict | ProfileValidationError
    ) -> JSONResponse:
        return auth_error_response(profile_error(exc))

    @application.middleware("http")
    async def security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        route_template = getattr(route, "path", "<unmatched>")
        HTTP_LOGGER.info(
            "http_request method=%s route=%s status=%d",
            request.method,
            route_template,
            response.status_code,
        )
        return response

    @application.get("/healthz")
    def health() -> dict[str, object]:
        return {"status": "ok", "iteration": 1}

    @application.get("/readyz")
    def ready() -> JSONResponse:
        content = readiness.snapshot()
        worker = getattr(application.state, "plan_worker", None)
        if content.get("mode") == "managed" and content["status"] == "ready":
            if worker is None or not worker.thread.is_alive():
                content.update(status="failed", workerError="worker_unavailable")
            elif worker.last_error is not None:
                content.update(status="failed", workerError=worker.last_error)
        return JSONResponse(
            status_code=200 if content["status"] == "ready" else 503, content=content
        )

    application.include_router(create_auth_router(cookie_settings))
    application.include_router(create_profile_router(cookie_settings))
    application.include_router(create_plan_router(cookie_settings))
    application.include_router(create_package_admission_router(cookie_settings))
    application.post(
        "/api/v1/blueprints/validate",
        dependencies=[Depends(admin_csrf_dependency(cookie_settings))],
    )(validate_blueprint)
    configured_frontend = os.environ.get("GITFERRY_FRONTEND_DIR")
    if frontend_dir is None and configured_frontend:
        frontend_dir = Path(configured_frontend)
    if frontend_dir is not None:
        application.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
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
