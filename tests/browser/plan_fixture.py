"""Browser-only synchronization around a real plan worker and simulated remote write."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from fastapi import FastAPI
from gitferry.connection_probe import approve_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.operations import OperationExecutor
from gitferry.plan_application import prepare_transfer_plan
from gitferry.state_store import OriginKey, StateStore, TargetConfirmation

STARTED = threading.Event()
RELEASE = threading.Event()


class BrowserPlanAdapter:
    def __init__(self, *args: Any):
        pass

    def __enter__(self) -> BrowserPlanAdapter:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def preflight(self, plan: dict[str, Any]) -> None:
        if plan["round_key"] != "browser-disconnect":
            raise ValueError("only the synthetic browser selection is supported")

    def apply(self, plan: dict[str, Any], executor: OperationExecutor) -> None:
        def accepted(_: dict[str, Any]) -> TargetConfirmation:
            STARTED.set()
            if not RELEASE.wait(timeout=15):
                raise RuntimeError("browser test did not release the simulated remote call")
            return TargetConfirmation("101", 1)

        executor.execute(plan["operations"][0]["operation_id"], accepted)


def attach_plan_fixture(application: FastAPI, state: StateStore) -> None:
    @application.post("/__test__/seed-plan")
    def seed_plan() -> dict[str, Any]:
        STARTED.clear()
        RELEASE.clear()
        profiles = ProfileService(state)
        profiles.create(
            ConnectionProfile(
                "browser-target",
                "Browser target",
                "https://browser.invalid",
                "browser-install",
                "browser-credential",
            )
        )
        identity = json.dumps(
            {
                "account_id": "1",
                "login": "technical",
                "ca_digest": "system-trust",
                "certificate_sha256": "synthetic",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with state.transaction() as connection:
            connection.execute(
                "INSERT INTO connection_checks VALUES (?,?,?,?,?)",
                ("browser-target", 1, identity, identity, "now"),
            )
        approve_connection(
            profiles,
            "browser-target",
            expected_revision=1,
            evidence_digest=hashlib.sha256(identity.encode()).hexdigest(),
        )
        state.register_realm("browser-realm", "Browser realm", "synthetic")
        state.confirm_identity_binding(
            binding_id="browser-author",
            source_realm_key="browser-realm",
            source_login="author",
            source_external_id="external",
            source_ou_path="OU=Synthetic",
            target_instance_key="browser-target",
            target_account_id=2,
            target_login="author",
            mapping_version=1,
        )
        origin = state.register_origin(OriginKey("browser-target", "org/repo", "issue", "1", 1))
        return prepare_transfer_plan(
            state,
            {
                "version": 1,
                "target_instance_key": "browser-target",
                "round_key": "browser-disconnect",
                "author_credential_refs": {"browser-author": "author-credential"},
                "metadata": [
                    {
                        "origin_id": origin.id,
                        "repository": "org/repo",
                        "author": {
                            "realm_key": "browser-realm",
                            "login": "author",
                            "external_id": "external",
                            "ou_path": "OU=Synthetic",
                        },
                        "values": {
                            "title": "Browser disconnect",
                            "body": "Synthetic",
                            "state": "open",
                            "labels": [],
                        },
                    }
                ],
            },
        )

    @application.get("/__test__/plan-gate")
    def gate() -> dict[str, bool]:
        return {"started": STARTED.is_set()}

    @application.post("/__test__/release-plan")
    def release() -> dict[str, bool]:
        RELEASE.set()
        return {"released": True}
