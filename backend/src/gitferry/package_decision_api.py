"""Administrator-controlled local decisions, separate from transport fact admission."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from gitferry.application_package import PackageError
from gitferry.auth_api import AuthBoundaryError, AuthCookieSettings, admin_csrf_dependency
from gitferry.connection_probe import ConnectionUnavailable
from gitferry.connection_profiles import ProfileConflict
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.package_admission import PackageAdmissionConflict
from gitferry.package_api import _admin_read, _service
from gitferry.package_decisions import (
    AccountRequest,
    BindingRequest,
    PackageDecisions,
    RealmRequest,
    SenderRequest,
    TargetRequest,
)
from gitferry.plan_api import _json_body
from gitferry.state_store import BindingConflict, OperationBlocked, StateStoreError
from gitferry.transfer_plans import PlanConflict
from gitferry.transport import ManifestConflict


def create_package_decision_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/packages", dependencies=[Depends(_admin_read(settings))])
    write = [Depends(admin_csrf_dependency(settings))]

    async def run(
        request: Request, action: Callable[[PackageDecisions, dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            value = await _json_body(request)
            service = PackageDecisions(_service(request))
            return await run_in_threadpool(action, service, value)
        except AuthBoundaryError:
            raise
        except (
            BindingConflict,
            PlanConflict,
            PackageAdmissionConflict,
            ProfileConflict,
            ManifestConflict,
            OperationBlocked,
            ProvisioningTargetConflict,
        ):
            raise AuthBoundaryError(
                409,
                "local_decision_conflict",
                "Package, account or local approval changed; review prerequisites",
            ) from None
        except ConnectionUnavailable:
            raise AuthBoundaryError(
                422,
                "account_unavailable",
                "Check the local credential/CA reference and selected target account",
            ) from None
        except (PackageError, ValueError, KeyError, TypeError):
            raise AuthBoundaryError(
                400, "invalid_decision", "Invalid local decision or prerequisite is missing"
            ) from None
        except (StateStoreError, sqlite3.Error, OSError):
            raise AuthBoundaryError(
                409,
                "local_state_conflict",
                "Local registration conflicts or storage is unavailable",
            ) from None

    @router.post("/{package_ref}/prerequisites", dependencies=write)
    async def prerequisites(package_ref: str, request: Request) -> dict[str, Any]:
        return await run(
            request,
            lambda service, value: service.prerequisites(
                package_ref, TargetRequest.model_validate(value)
            ),
        )

    @router.post("/{package_ref}/confirm-sender", dependencies=write)
    async def sender(package_ref: str, request: Request) -> dict[str, Any]:
        return await run(
            request,
            lambda service, value: service.confirm_sender(
                package_ref, SenderRequest.model_validate(value)
            ),
        )

    @router.post("/{package_ref}/confirm-realm", dependencies=write)
    async def realm(package_ref: str, request: Request) -> dict[str, Any]:
        return await run(
            request,
            lambda service, value: service.confirm_realm(
                package_ref, RealmRequest.model_validate(value)
            ),
        )

    @router.post("/{package_ref}/check-account", dependencies=write)
    async def account(package_ref: str, request: Request) -> dict[str, Any]:
        return await run(
            request,
            lambda service, value: service.check_account(
                package_ref, AccountRequest.model_validate(value)
            ),
        )

    @router.post("/{package_ref}/confirm-binding", dependencies=write)
    async def binding(package_ref: str, request: Request) -> dict[str, Any]:
        return await run(
            request,
            lambda service, value: service.confirm_binding(
                package_ref, BindingRequest.model_validate(value)
            ),
        )

    return router
