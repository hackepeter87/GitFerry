"""Authenticated HTTP boundary for local package admission and planning."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request

from gitferry.application_package import PackageError
from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    require_authenticated,
)
from gitferry.package_admission import PackageAdmissionConflict, PackageAdmissions
from gitferry.plan_api import _json_body
from gitferry.state_store import (
    MappingConflict,
    OperationBlocked,
    OperationConflict,
    StateStoreError,
)
from gitferry.transfer_plans import PlanConflict
from gitferry.transport import ManifestConflict


def _admin_read(settings: AuthCookieSettings) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not require_authenticated(request, settings).is_admin:
            raise AuthBoundaryError(403, "admin_required", "Administrator access required")

    return dependency


def _service(request: Request) -> PackageAdmissions:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        return PackageAdmissions(runtime.store, runtime.paths)
    raise AuthBoundaryError(503, "state_unavailable", "Local state is unavailable")


def _request_fields(
    value: dict[str, Any], *, required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    optional = optional or set()
    if not required <= set(value) or not set(value) <= required | optional:
        raise AuthBoundaryError(400, "invalid_request", "Invalid package admission request")
    target = value["target_instance_key"]
    revision = value["profile_revision"]
    if (
        not isinstance(target, str)
        or not target
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
    ):
        raise AuthBoundaryError(400, "invalid_request", "Invalid package admission request")
    result = {"target_instance_key": target, "profile_revision": revision}
    for name in ("expected_digest", "expected_review_digest"):
        if name in value and (not isinstance(value[name], str) or not value[name]):
            raise AuthBoundaryError(400, "invalid_request", "Invalid package admission request")
        if name in value:
            result[name] = value[name]
    for name in ("include_metadata", "apply_members"):
        if name in value and not isinstance(value[name], bool):
            raise AuthBoundaryError(400, "invalid_request", "Invalid package admission request")
        if name in value:
            result[name] = value[name]
    if "author_credential_refs" in value:
        refs = value["author_credential_refs"]
        if not isinstance(refs, dict) or any(
            not isinstance(key, str) or not key or not isinstance(ref, str) or not ref
            for key, ref in refs.items()
        ):
            raise AuthBoundaryError(400, "invalid_request", "Invalid package admission request")
        result["author_credential_refs"] = refs
    elif "author_credential_refs" in optional:
        result["author_credential_refs"] = {}
    if "include_metadata" in optional and "include_metadata" not in result:
        result["include_metadata"] = True
    if "apply_members" in optional and "apply_members" not in result:
        result["apply_members"] = False
    return result


def create_package_admission_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/packages", dependencies=[Depends(_admin_read(settings))])
    write = [Depends(admin_csrf_dependency(settings))]

    @router.get("")
    def receipts(request: Request) -> list[dict[str, Any]]:
        try:
            return _service(request).list()
        except StateStoreError:
            raise AuthBoundaryError(
                503, "state_unavailable", "Local state is unavailable"
            ) from None

    @router.post("/{package_ref}/review", dependencies=write)
    async def review(package_ref: str, request: Request) -> dict[str, Any]:
        return await _run(
            request,
            lambda service, value: service.review(
                package_ref,
                **_request_fields(value, required={"target_instance_key", "profile_revision"}),
            ),
        )

    @router.post("/{package_ref}/admit", dependencies=write)
    async def admit(package_ref: str, request: Request) -> dict[str, Any]:
        return await _run(
            request,
            lambda service, value: service.admit(
                package_ref,
                **_request_fields(
                    value, required={"target_instance_key", "profile_revision", "expected_digest"}
                ),
            ),
        )

    @router.post("/{package_ref}/plan-review", dependencies=write)
    async def plan_review(package_ref: str, request: Request) -> dict[str, Any]:
        return await _run(
            request,
            lambda service, value: service.plan_review(
                package_ref,
                **_request_fields(
                    value,
                    required={"target_instance_key", "profile_revision"},
                    optional={
                        "include_metadata",
                        "apply_members",
                        "author_credential_refs",
                    },
                ),
            ),
        )

    @router.post("/{package_ref}/prepare-plan", dependencies=write)
    async def prepare_plan(package_ref: str, request: Request) -> dict[str, Any]:
        return await _run(
            request,
            lambda service, value: service.prepare_plan(
                package_ref,
                **_request_fields(
                    value,
                    required={"target_instance_key", "profile_revision", "expected_review_digest"},
                    optional={
                        "include_metadata",
                        "apply_members",
                        "author_credential_refs",
                    },
                ),
            ),
        )

    return router


async def _run(
    request: Request, operation: Callable[[PackageAdmissions, dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    try:
        value = await _json_body(request)
        return operation(_service(request), value)
    except AuthBoundaryError:
        raise
    except (
        PackageAdmissionConflict,
        PlanConflict,
        ManifestConflict,
        MappingConflict,
        OperationConflict,
        OperationBlocked,
    ):
        raise AuthBoundaryError(
            409, "package_conflict", "Package admission cannot be completed"
        ) from None
    except (PackageError, ValueError, KeyError, TypeError):
        raise AuthBoundaryError(
            400, "invalid_package", "Package admission request is invalid"
        ) from None
    except (StateStoreError, sqlite3.Error, OSError):
        raise AuthBoundaryError(503, "state_unavailable", "Local state is unavailable") from None


create_package_router = create_package_admission_router

__all__ = ["create_package_admission_router", "create_package_router"]
