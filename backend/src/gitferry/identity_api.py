"""Authenticated, CSRF-protected local identity decisions; no remote mutations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from gitferry.auth_api import (
    AuthBoundaryError,
    AuthCookieSettings,
    admin_csrf_dependency,
    require_authenticated,
)
from gitferry.connection_probe import ConnectionUnavailable
from gitferry.directory_claims import DirectoryClaimsError, DirectoryClaimsUnavailable
from gitferry.directory_settings import DirectorySettingsConflict
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.identity_application import (
    CandidateRequest,
    CheckAccountRequest,
    IdentityAction,
    IdentityApplication,
    RegisterRealm,
)
from gitferry.identity_decisions import IdentityDecisionError
from gitferry.package_admission import PackageAdmissionConflict
from gitferry.package_api import _admin_read, _service
from gitferry.plan_api import _json_body
from gitferry.state_store import BindingConflict, StateStoreError
from gitferry.transfer_plans import PlanConflict


def create_identity_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/identities", dependencies=[Depends(_admin_read(settings))])
    write = [Depends(admin_csrf_dependency(settings))]

    def service(request: Request) -> IdentityApplication:
        return IdentityApplication(_service(request).state)

    async def run(
        request: Request,
        action: Callable[[IdentityApplication, dict[str, Any]], Any],
        *,
        body: bool = True,
    ) -> Any:
        try:
            value = await _json_body(request) if body else {}
            return await run_in_threadpool(action, service(request), value)
        except (
            IdentityDecisionError,
            BindingConflict,
            PackageAdmissionConflict,
            PlanConflict,
            ProvisioningTargetConflict,
            DirectoryClaimsError,
            DirectorySettingsConflict,
        ):
            raise AuthBoundaryError(
                409,
                "identity_conflict",
                "Identity, target account or local approval changed. "
                "Refresh the decision; no binding was replaced.",
            ) from None
        except (ConnectionUnavailable, DirectoryClaimsUnavailable):
            raise AuthBoundaryError(
                422,
                "account_unavailable",
                "Target account cannot be verified. "
                "Check its status and local connection/CA references.",
            ) from None
        except (ValueError, TypeError, KeyError):
            raise AuthBoundaryError(
                400,
                "invalid_identity_decision",
                "Invalid identity decision or missing local realm.",
            ) from None
        except (StateStoreError, sqlite3.Error, OSError):
            raise AuthBoundaryError(
                409,
                "identity_state_unavailable",
                "Local identity state is unavailable; no approval was granted.",
            ) from None

    @router.get("")
    async def listing(request: Request) -> Any:
        return await run(request, lambda app, _: app.decisions.list_candidates(), body=False)

    @router.get("/realms")
    async def realms(request: Request) -> Any:
        def read(app: IdentityApplication, _: dict[str, Any]) -> Any:
            with app.state.read_connection() as connection:
                return [
                    dict(row)
                    for row in connection.execute(
                        "SELECT realm_key,display_name,external_marker "
                        "FROM realms ORDER BY realm_key"
                    )
                ]

        return await run(request, read, body=False)

    @router.post("/realms", dependencies=write)
    async def realm(request: Request) -> Any:
        return await run(
            request,
            lambda app, value: asdict(
                app.state.register_realm(**RegisterRealm.model_validate(value).model_dump())
            ),
        )

    @router.post("/check-account", dependencies=write)
    async def check(request: Request) -> Any:
        return await run(
            request, lambda app, value: app.check_account(CheckAccountRequest.model_validate(value))
        )

    @router.post("/candidates", dependencies=write)
    async def propose(request: Request) -> Any:
        return await run(
            request, lambda app, value: app.propose(CandidateRequest.model_validate(value))
        )

    @router.post("/{candidate_id}/decision", dependencies=write)
    async def decision(candidate_id: str, request: Request) -> Any:
        actor = require_authenticated(request, settings).account_id
        return await run(
            request,
            lambda app, value: app.decide(
                candidate_id, IdentityAction.model_validate(value), actor=actor
            ),
        )

    @router.get("/{candidate_id}/events")
    async def events(candidate_id: str, request: Request) -> Any:
        return await run(request, lambda app, _: app.decisions.events(candidate_id), body=False)

    return router
