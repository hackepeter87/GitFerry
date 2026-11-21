"""Bounded authenticated raw-file upload; no target writes or implicit admission."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from typing import Any, cast

import anyio
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from gitferry.application_package import PackageError
from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    require_authenticated,
)
from gitferry.connection_profiles import ProfileValidationError
from gitferry.operations import ExecutorBusy
from gitferry.package_uploads import PackageUploads, UploadConflict, UploadLimit
from gitferry.plan_api import _json_body
from gitferry.state_store import StateStoreError


def _service(request: Request) -> PackageUploads:
    service = getattr(request.app.state, "package_uploads", None)
    if service is None:
        raise AuthBoundaryError(503, "state_unavailable", "Local storage is unavailable")
    return cast(PackageUploads, service)


def _error(error: Exception) -> AuthBoundaryError:
    if isinstance(error, AuthBoundaryError):
        return error
    if isinstance(error, UploadLimit):
        return AuthBoundaryError(413, "upload_limit", "The archive exceeds the upload limit")
    if isinstance(error, ExecutorBusy):
        return AuthBoundaryError(
            409, "executor_busy", "Another operation is active. Retry this upload later."
        )
    if isinstance(error, UploadConflict):
        return AuthBoundaryError(
            409, "upload_conflict", "Upload conflicts with saved evidence or is incomplete"
        )
    if isinstance(error, (PackageError, ProfileValidationError, ValueError)):
        return AuthBoundaryError(
            422, "invalid_package", "The file is not a supported, intact GitFerry package"
        )
    if isinstance(error, KeyError):
        return AuthBoundaryError(404, "upload_not_found", "Upload not found")
    if isinstance(error, TimeoutError):
        return AuthBoundaryError(
            408, "upload_timeout", "Upload time limit reached; retry the same file"
        )
    if isinstance(error, (OSError, sqlite3.Error, StateStoreError)):
        return AuthBoundaryError(503, "storage_unavailable", "Local storage is unavailable")
    return AuthBoundaryError(500, "upload_failed", "The upload could not be completed")


def create_package_upload_router(settings: AuthCookieSettings) -> APIRouter:
    def read(request: Request) -> None:
        if not require_authenticated(request, settings).is_admin:
            raise AuthBoundaryError(403, "admin_required", "Administrator access required")

    router = APIRouter(prefix="/api/v1/package-uploads", dependencies=[Depends(read)])
    write = [Depends(admin_csrf_dependency(settings))]

    def run(action: Callable[[], dict[str, Any] | list[dict[str, Any]]]) -> Any:
        try:
            return action()
        except Exception as error:
            raise _error(error) from None

    @router.get("")
    def uploads(request: Request) -> Any:
        return run(lambda: _service(request).list())

    @router.get("/{upload_id}")
    def get(upload_id: str, request: Request) -> Any:
        return run(lambda: _service(request).get(upload_id))

    @router.put("/{upload_id}", dependencies=write)
    async def upload(upload_id: str, request: Request) -> dict[str, Any]:
        try:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/zip":
                raise AuthBoundaryError(415, "zip_required", "Send a GitFerry ZIP archive")
            declared = request.headers.get("x-gitferry-archive-size", "")
            if not declared.isascii() or not declared.isdecimal() or len(declared) > 12:
                raise AuthBoundaryError(
                    400, "size_required", "A valid archive byte size is required"
                )
            size = int(declared)
            length = request.headers.get("content-length")
            if length is not None and length != declared:
                raise AuthBoundaryError(
                    400, "size_mismatch", "Content length differs from archive size"
                )
            service = _service(request)
            timeout = float(os.environ.get("GITFERRY_UPLOAD_TIMEOUT_SECONDS", "900"))
            if not 0 < timeout <= 86400:
                raise AuthBoundaryError(503, "upload_configuration", "Upload time limit is invalid")
            with service.receiving(upload_id, size) as sink:
                with anyio.fail_after(timeout):
                    async for chunk in request.stream():
                        await run_in_threadpool(sink.write, chunk)
                # The completed body is processed independently of browser
                # polling. No entire-file allocation and no target API call.
                return await run_in_threadpool(sink.finish)
        except Exception as error:
            raise _error(error) from None

    @router.post("/{upload_id}/resume", dependencies=write)
    async def resume(upload_id: str, request: Request) -> dict[str, Any]:
        try:
            if await _json_body(request):
                raise AuthBoundaryError(400, "invalid_request", "Resume accepts an empty object")
            return await run_in_threadpool(_service(request).resume, upload_id)
        except Exception as error:
            raise _error(error) from None

    return router
