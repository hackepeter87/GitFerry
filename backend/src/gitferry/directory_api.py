"""Local directory intent administration; HTTP never executes server commands."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    require_authenticated,
)
from gitferry.blueprints import validate_text
from gitferry.directory_execution import DirectoryBootstrapService
from gitferry.directory_settings import DirectorySettingsConflict, DirectorySettingsService
from gitferry.package_api import _admin_read
from gitferry.plan_api import _json_body
from gitferry.state_store import StateStoreError


class SaveDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    instance_key: Annotated[str, Field(min_length=1, max_length=64)]
    source: dict[str, Any]
    expected_revision: Annotated[int, Field(ge=1)] | None = None


class ImportDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    instance_key: Annotated[str, Field(min_length=1, max_length=64)]
    content: Annotated[str, Field(max_length=65536)]
    expected_revision: Annotated[int, Field(ge=1)] | None = None


def create_directory_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/directories", dependencies=[Depends(_admin_read(settings))])
    writes = [Depends(admin_csrf_dependency(settings))]

    def with_journal(service: DirectorySettingsService, record: dict[str, Any]) -> dict[str, Any]:
        journal = DirectoryBootstrapService(service.store).get(
            record["instance_key"], record["source_name"]
        )
        # Historical local execution evidence, not approval of a later edited configuration.
        return {**record, "bootstrap": asdict(journal) if journal else None}

    async def run(request: Request, action: Callable[[DirectorySettingsService], Any]) -> Any:
        try:
            runtime = getattr(request.app.state, "runtime", None)
            auth = getattr(request.app.state, "auth", None)
            state = (
                runtime.store if runtime is not None else auth.state if auth is not None else None
            )
            if state is None:
                raise AuthBoundaryError(503, "state_unavailable", "Local state is unavailable")
            return await run_in_threadpool(action, DirectorySettingsService(state))
        except DirectorySettingsConflict:
            raise AuthBoundaryError(
                409,
                "directory_conflict",
                "Directory intent or local authority changed. Refresh before editing.",
            ) from None
        except (ValueError, TypeError, KeyError):
            raise AuthBoundaryError(
                400,
                "invalid_directory",
                "Invalid blueprint, unregistered realm or missing local prerequisite.",
            ) from None
        except (sqlite3.Error, StateStoreError, OSError):
            raise AuthBoundaryError(
                409,
                "directory_unavailable",
                "Local directory state is unavailable; no server command was executed.",
            ) from None

    @router.get("")
    async def listing(request: Request) -> Any:
        return await run(
            request, lambda service: [with_journal(service, row) for row in service.list()]
        )

    @router.post("", dependencies=writes)
    async def save(request: Request) -> Any:
        value = await _json_body(request)
        actor = require_authenticated(request, settings).account_id

        def action(service: DirectorySettingsService) -> Any:
            data = SaveDirectory.model_validate(value)
            return with_journal(
                service,
                service.save(
                    data.instance_key,
                    data.source,
                    expected_revision=data.expected_revision,
                    actor=actor,
                ),
            )

        return await run(request, action)

    @router.post("/import", dependencies=writes)
    async def import_blueprint(request: Request) -> Any:
        value = await _json_body(request)
        actor = require_authenticated(request, settings).account_id

        def action(service: DirectorySettingsService) -> Any:
            data = ImportDirectory.model_validate(value)
            return with_journal(
                service,
                service.import_blueprint(
                    data.instance_key,
                    validate_text(data.content),
                    expected_revision=data.expected_revision,
                    actor=actor,
                ),
            )

        return await run(request, action)

    @router.get("/{instance_key}/{source_name}/export")
    async def export(instance_key: str, source_name: str, request: Request) -> Any:
        return await run(request, lambda service: service.export(instance_key, source_name))

    return router
