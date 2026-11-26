"""Protected inventory commands; HTTP requests never execute remote writes."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from gitferry.auth_api import AuthBoundaryError, AuthCookieSettings, admin_csrf_dependency
from gitferry.connection_profiles import ProfileConflict
from gitferry.content_inventory import CONTENT_CLASSES, TRANSFER_CLASSES, InventoryError
from gitferry.inventory_jobs import InventoryJobs
from gitferry.plan_api import _json_body
from gitferry.source_export_api import _admin_read, _worker
from gitferry.state_codec import canonical_json
from gitferry.state_store import StateStoreError


def _jobs(request: Request) -> InventoryJobs:
    service = getattr(request.app.state, "inventory_jobs", None)
    if not isinstance(service, InventoryJobs):
        raise AuthBoundaryError(503, "state_unavailable", "Local state is unavailable")
    return service


def _error(error: Exception) -> AuthBoundaryError:
    if isinstance(error, KeyError):
        return AuthBoundaryError(404, "inventory_not_found", "Inventory not found")
    if isinstance(error, (InventoryError, ProfileConflict, ValueError)):
        return AuthBoundaryError(
            409, "inventory_conflict", "Inventory selection or source approval needs review"
        )
    if isinstance(error, (StateStoreError, sqlite3.Error, OSError)):
        return AuthBoundaryError(503, "state_unavailable", "Local state is unavailable")
    return AuthBoundaryError(500, "inventory_failed", "Inventory could not be processed")


def create_inventory_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/inventories", tags=["inventory"])
    read = _admin_read(settings)
    write = admin_csrf_dependency(settings)

    @router.get("", dependencies=[Depends(read)])
    def listing(request: Request) -> list[dict[str, Any]]:
        try:
            return _jobs(request).list()
        except Exception as error:
            raise _error(error) from None

    @router.post("", dependencies=[Depends(write)], status_code=202)
    async def create(request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        if (
            set(body) != {"inventory_id", "request"}
            or not isinstance(body["inventory_id"], str)
            or not isinstance(body["request"], dict)
        ):
            raise AuthBoundaryError(400, "invalid_request", "Invalid inventory request")
        worker = _worker(request)
        try:
            result = _jobs(request).create(body["inventory_id"], body["request"])
            worker.wake.set()
            return result
        except Exception as error:
            raise _error(error) from None

    @router.get("/capabilities", dependencies=[Depends(read)])
    def capabilities() -> dict[str, Any]:
        return {
            "classes": list(CONTENT_CLASSES),
            "supported": sorted(TRANSFER_CLASSES | {"lfs", "wiki"}),
            "schema_version": 2,
        }

    @router.get("/{inventory_id}", dependencies=[Depends(read)])
    def show(inventory_id: str, request: Request) -> dict[str, Any]:
        try:
            return _jobs(request).get(inventory_id)
        except Exception as error:
            raise _error(error) from None

    @router.get("/{inventory_id}/document", dependencies=[Depends(read)])
    def document(inventory_id: str, request: Request) -> Response:
        try:
            result = _jobs(request).get(inventory_id)
            if result["document"] is None:
                raise InventoryError("inventory has no confirmed document")
            return Response(
                canonical_json(result["document"]) + "\n",
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{inventory_id}.json"'},
            )
        except Exception as error:
            raise _error(error) from None

    return router
