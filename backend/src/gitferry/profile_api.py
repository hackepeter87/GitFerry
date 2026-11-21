"""Authenticated, bounded HTTP boundary for local connection configuration."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request

from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    require_authenticated,
)
from gitferry.connection_probe import ConnectionUnavailable, approve_connection, probe_connection
from gitferry.connection_profiles import (
    ConnectionProfile,
    ProfileConflict,
    ProfileService,
    ProfileValidationError,
)
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.source_catalog import browse_source

MAX_CONFIGURATION_BYTES = 256 * 1024


def parse_configuration(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_CONFIGURATION_BYTES:
        raise ProfileValidationError("Connection configuration exceeds 256 KiB")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProfileValidationError("Duplicate configuration field")
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError):
        raise ProfileValidationError("Invalid connection JSON") from None
    if not isinstance(document, dict):
        raise ProfileValidationError("Connection configuration must be an object")
    return document


async def _body(request: Request, fields: set[str]) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise AuthBoundaryError(415, "json_required", "JSON request required")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_CONFIGURATION_BYTES:
            raise AuthBoundaryError(413, "request_too_large", "Connection request is too large")
        data.extend(chunk)
    value = parse_configuration(bytes(data))
    if set(value) != fields:
        raise ProfileValidationError("Unknown or missing connection fields")
    return value


def create_profile_router(settings: AuthCookieSettings) -> APIRouter:
    def read_access(request: Request) -> None:
        if not require_authenticated(request, settings).is_admin:
            raise AuthBoundaryError(403, "admin_required", "Administrator access required")

    router = APIRouter(prefix="/api/v1/connections", dependencies=[Depends(read_access)])
    write = [Depends(admin_csrf_dependency(settings))]

    def service(request: Request) -> ProfileService:
        return ProfileService(request.app.state.auth.state)

    @router.get("")
    def profiles(request: Request) -> list[dict[str, Any]]:
        return [asdict(item) for item in service(request).list_profiles()]

    @router.get("/export")
    def export(request: Request) -> dict[str, Any]:
        return service(request).export_configuration()

    @router.post("/import", dependencies=write)
    async def import_profiles(request: Request) -> list[dict[str, Any]]:
        value = await _body(request, {"format", "version", "profiles"})
        return [asdict(item) for item in service(request).import_configuration(value)]

    @router.post("", dependencies=write, status_code=201)
    async def create(request: Request) -> dict[str, Any]:
        value = await _body(
            request,
            {
                "display_name",
                "base_url",
                "installation_marker",
                "credential_ref",
                "ca_ref",
            },
        )
        profile = ConnectionProfile(instance_key=str(uuid.uuid4()), **value)
        return asdict(service(request).create(profile))

    @router.patch("/{key}", dependencies=write)
    async def update(key: str, request: Request) -> dict[str, Any]:
        value = await _body(
            request, {"expected_revision", "display_name", "credential_ref", "ca_ref"}
        )
        return asdict(service(request).update(key, **value))

    @router.post("/{key}/retire", dependencies=write)
    async def retire(key: str, request: Request) -> dict[str, Any]:
        value = await _body(request, {"expected_revision"})
        return asdict(service(request).retire(key, **value))

    @router.post("/{key}/replace-installation", dependencies=write)
    async def rebuild(key: str, request: Request) -> dict[str, Any]:
        value = await _body(request, {"expected_revision", "installation_marker"})
        return asdict(service(request).replace_installation(key, **value))

    @router.post("/{key}/test", dependencies=write)
    async def test_connection(key: str, request: Request) -> dict[str, Any]:
        from starlette.concurrency import run_in_threadpool

        value = await _body(request, {"expected_revision"})
        try:
            return await run_in_threadpool(probe_connection, service(request), key, **value)
        except ConnectionUnavailable as exc:
            raise AuthBoundaryError(422, "connection_unavailable", str(exc)) from None

    @router.post("/{key}/approve", dependencies=write)
    async def approve(key: str, request: Request) -> dict[str, Any]:
        value = await _body(request, {"expected_revision", "evidence_digest"})
        approve_connection(service(request), key, **value)
        return asdict(service(request).get(key))

    @router.post("/{key}/browse", dependencies=write)
    async def browse(key: str, request: Request) -> dict[str, Any]:
        from starlette.concurrency import run_in_threadpool

        value = await _body(request, {"expected_revision", "kind", "organization", "page"})
        try:
            return await run_in_threadpool(browse_source, service(request), key, **value)
        except (ConnectionUnavailable, ProvisioningTargetConflict):
            raise AuthBoundaryError(
                422,
                "source_unavailable",
                "Source page unavailable. Check source access and connection approval; "
                "retry this page.",
            ) from None

    return router


def profile_error(error: ProfileConflict | ProfileValidationError) -> AuthBoundaryError:
    return AuthBoundaryError(
        409 if isinstance(error, ProfileConflict) else 422,
        "profile_conflict" if isinstance(error, ProfileConflict) else "invalid_profile",
        str(error),
    )
