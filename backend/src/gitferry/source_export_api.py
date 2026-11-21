"""Authenticated HTTP boundary for source export jobs."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from typing import Any, BinaryIO, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    require_authenticated,
)
from gitferry.connection_profiles import ProfileConflict
from gitferry.plan_api import _json_body
from gitferry.source_export_jobs import SourceExportJobs
from gitferry.source_exports import SourceExportConflict
from gitferry.state_store import StateStoreError


def _admin_read(settings: AuthCookieSettings) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not require_authenticated(request, settings).is_admin:
            raise AuthBoundaryError(403, "admin_required", "Administrator access required")

    return dependency


def _service(request: Request) -> SourceExportJobs:
    jobs = getattr(request.app.state, "export_jobs", None)
    if jobs is not None:
        return cast(SourceExportJobs, jobs)
    raise AuthBoundaryError(503, "state_unavailable", "Local state is unavailable")


def _worker(request: Request) -> Any:
    worker = getattr(request.app.state, "plan_worker", None)
    thread = getattr(worker, "thread", None)
    if worker is None or thread is None or not thread.is_alive():
        raise AuthBoundaryError(503, "worker_unavailable", "Export worker is unavailable")
    return worker


def _invalid() -> AuthBoundaryError:
    return AuthBoundaryError(400, "invalid_request", "Invalid source export request")


def _map_error(error: BaseException) -> AuthBoundaryError:
    # Never expose service exception text: it can contain paths or remote details.
    if isinstance(error, (SourceExportConflict, ProfileConflict)):
        return AuthBoundaryError(409, "export_conflict", "Source export cannot be completed")
    if isinstance(error, KeyError):
        return AuthBoundaryError(404, "export_not_found", "Source export not found")
    if isinstance(error, (StateStoreError, sqlite3.Error, OSError)):
        return AuthBoundaryError(503, "state_unavailable", "Local state is unavailable")
    if isinstance(error, ValueError):
        return _invalid()
    return AuthBoundaryError(500, "export_error", "Source export could not be processed")


def _strict_create(value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if set(value) != {"export_id", "request"}:
        raise _invalid()
    export_id, request = value["export_id"], value["request"]
    if not isinstance(export_id, str) or not export_id or not isinstance(request, dict):
        raise _invalid()
    return export_id, request


def _strict_empty(value: dict[str, Any]) -> None:
    if value:
        raise _invalid()


def _stream(file: BinaryIO, close: Callable[[], None]) -> Iterator[bytes]:
    try:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                return
            yield chunk
    finally:
        close()


def create_source_export_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/exports", dependencies=[Depends(_admin_read(settings))])
    write = [Depends(admin_csrf_dependency(settings))]

    @router.get("")
    def exports(request: Request) -> list[dict[str, Any]]:
        try:
            return _service(request).list()
        except Exception as error:
            if isinstance(error, AuthBoundaryError):
                raise
            raise _map_error(error) from None

    @router.get("/recipients")
    def recipients(request: Request) -> list[dict[str, Any]]:
        try:
            return _service(request).recipients()
        except Exception as error:
            if isinstance(error, AuthBoundaryError):
                raise
            raise _map_error(error) from None

    @router.get("/{export_id}")
    def get_export(export_id: str, request: Request) -> dict[str, Any]:
        try:
            return _service(request).get(export_id)
        except Exception as error:
            if isinstance(error, AuthBoundaryError):
                raise
            raise _map_error(error) from None

    @router.post("", dependencies=write, status_code=202)
    async def create(request: Request) -> dict[str, Any]:
        try:
            worker = _worker(request)
            export_id, value = _strict_create(await _json_body(request))
            result = _service(request).create(export_id, value)
            worker.wake.set()
            return result
        except AuthBoundaryError:
            raise
        except Exception as error:
            raise _map_error(error) from None

    @router.post("/{export_id}/queue", dependencies=write, status_code=202)
    async def queue(export_id: str, request: Request) -> dict[str, Any]:
        try:
            _worker(request)
            _strict_empty(await _json_body(request))
            result = _service(request).queue(export_id)
            request.app.state.plan_worker.wake.set()
            return result
        except AuthBoundaryError:
            raise
        except Exception as error:
            raise _map_error(error) from None

    @router.get("/{export_id}/download")
    def download(export_id: str, request: Request) -> StreamingResponse:
        try:
            file, job = _service(request).open_download(export_id)
            digest = job.get("archive_sha256")
            size = job.get("archive_size")
            if not isinstance(digest, str) or not isinstance(size, int) or size < 0:
                file.close()
                raise _invalid()
            headers = {
                "Content-Length": str(size),
                "X-Archive-SHA256": digest,
                "Content-Disposition": 'attachment; filename="source-export.zip"',
            }
            closed = False

            def close() -> None:
                nonlocal closed
                if not closed:
                    closed = True
                    file.close()

            return StreamingResponse(
                _stream(file, close),
                media_type="application/zip",
                headers=headers,
                background=BackgroundTask(close),
            )
        except AuthBoundaryError:
            raise
        except Exception as error:
            raise _map_error(error) from None

    return router


__all__ = ["create_source_export_router"]
