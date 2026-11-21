"""Authenticated HTTP boundary for durable transfer plans."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request

from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    require_authenticated,
)
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.plan_application import prepare_transfer_plan
from gitferry.profile_api import MAX_CONFIGURATION_BYTES, parse_configuration
from gitferry.state_store import OperationBlocked, StateStoreError
from gitferry.transfer_plans import PlanConflict, PlanService


def _admin_read(settings: AuthCookieSettings) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not require_authenticated(request, settings).is_admin:
            raise AuthBoundaryError(403, "admin_required", "Administrator access required")

    return dependency


async def _json_body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise AuthBoundaryError(415, "json_required", "JSON request required")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_CONFIGURATION_BYTES:
            raise AuthBoundaryError(413, "request_too_large", "Plan request is too large")
        body.extend(chunk)
    return parse_configuration(bytes(body))


def create_plan_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/plans", dependencies=[Depends(_admin_read(settings))])
    write = [Depends(admin_csrf_dependency(settings))]

    def service(request: Request) -> PlanService:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is not None:
            return PlanService(runtime.store)
        auth = getattr(request.app.state, "auth", None)
        if auth is None:
            raise AuthBoundaryError(503, "state_unavailable", "Local state is unavailable")
        return PlanService(auth.state)

    def worker(request: Request) -> Any:
        value = getattr(request.app.state, "plan_worker", None)
        if value is None or value.thread.is_alive() is False:
            raise AuthBoundaryError(503, "worker_unavailable", "Plan worker is unavailable")
        return value

    @router.get("")
    def plans(request: Request) -> list[dict[str, Any]]:
        try:
            return service(request).list()
        except (PlanConflict, StateStoreError):
            raise AuthBoundaryError(
                503, "state_unavailable", "Local state is unavailable"
            ) from None

    @router.get("/{plan_id}")
    def get_plan(plan_id: str, request: Request) -> dict[str, Any]:
        try:
            return service(request).get(plan_id)
        except PlanConflict:
            raise AuthBoundaryError(404, "plan_not_found", "Transfer plan not found") from None
        except StateStoreError:
            raise AuthBoundaryError(
                503, "state_unavailable", "Local state is unavailable"
            ) from None

    @router.post("/prepare", dependencies=write)
    async def prepare(request: Request) -> dict[str, Any]:
        try:
            return prepare_transfer_plan(service(request).store, await _json_body(request))
        except (ValueError, KeyError, OperationBlocked, ProvisioningTargetConflict):
            raise AuthBoundaryError(
                422, "invalid_plan", "Transfer plan selection is invalid or unavailable"
            ) from None
        except StateStoreError:
            raise AuthBoundaryError(
                503, "state_unavailable", "Local state is unavailable"
            ) from None

    @router.post("/{plan_id}/queue", dependencies=write)
    async def queue(plan_id: str, request: Request) -> dict[str, Any]:
        worker(request)
        try:
            value = await _json_body(request)
            if set(value) not in ({"digest"}, {"digest", "preview_generation"}) or not isinstance(
                value["digest"], str
            ):
                raise PlanConflict("Invalid queue request")
            if "preview_generation" in value and (
                type(value["preview_generation"]) is not int or value["preview_generation"] < 1
            ):
                raise PlanConflict("Invalid prerequisite review generation")
            result = service(request).queue(
                plan_id, value["digest"], preview_generation=value.get("preview_generation")
            )
            request.app.state.plan_worker.wake.set()
            return result
        except PlanConflict:
            raise AuthBoundaryError(
                409, "plan_conflict", "Transfer plan cannot be queued"
            ) from None
        except StateStoreError:
            raise AuthBoundaryError(
                503, "state_unavailable", "Local state is unavailable"
            ) from None

    @router.post("/{plan_id}/preview", dependencies=write)
    async def preview(plan_id: str, request: Request) -> dict[str, Any]:
        worker(request)
        try:
            value = await _json_body(request)
            if set(value) != {"digest"} or not isinstance(value["digest"], str):
                raise PlanConflict("Invalid prerequisite review request")
            result = service(request).request_preview(plan_id, value["digest"])
            request.app.state.plan_worker.wake.set()
            return result
        except PlanConflict:
            raise AuthBoundaryError(
                409,
                "preview_conflict",
                "Review the current plan and local approval before checking the target",
            ) from None
        except StateStoreError:
            raise AuthBoundaryError(
                503, "state_unavailable", "Local state is unavailable"
            ) from None

    @router.post("/{plan_id}/cancel", dependencies=write)
    def cancel(plan_id: str, request: Request) -> dict[str, Any]:
        try:
            return service(request).cancel(plan_id)
        except PlanConflict:
            raise AuthBoundaryError(404, "plan_not_found", "Transfer plan not found") from None
        except StateStoreError:
            raise AuthBoundaryError(
                503, "state_unavailable", "Local state is unavailable"
            ) from None

    return router


__all__ = ["create_plan_router"]
