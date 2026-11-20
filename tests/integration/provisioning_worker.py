"""Apply one bounded provisioning plan inside the isolated target container.

The plan format is intentionally small and integration-only.  It contains the
source origin identities, explicit target account bindings, and one selected
repository bundle.  No credential is accepted from or written to the plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from gitferry.git_bundle import import_git_bundle
from gitferry.gitea_provisioning import GiteaProvisioningClient
from gitferry.operations import OperationBlocked, OperationExecutor, OutcomeUnknownError
from gitferry.provisioning import (
    BundleIntent,
    ProvisioningMember,
    ProvisioningPlan,
    ProvisioningTeam,
    provision_selected_repository,
)
from gitferry.state_store import OriginKey, StateStore, TargetConfirmation

DATABASE = Path("/state/provisioning.sqlite3")
PACKAGE = Path("/package")
TARGET_URL = os.environ.get("GITFERRY_GITEA_URL", "http://target:3000")
TARGET_INSTANCE = "gitea-b-integration"
SOURCE_INSTANCE = "gitea-a-integration"
TARGET_USER = "gitferry-admin"
GIT = shutil.which("git")
if GIT is None:
    raise RuntimeError("git executable is required for provisioning")


def _required(value: Any, label: str) -> Any:
    if value is None:
        raise ValueError(f"plan is missing {label}")
    return value


def _origin(state: StateStore, item: dict[str, Any], object_type: str, scope: str):
    return state.register_origin(
        OriginKey(
            str(item.get("source_instance_key", SOURCE_INSTANCE)),
            str(item.get("repository_scope", scope)),
            object_type,
            str(_required(item.get("origin_global_id"), f"{object_type}.origin_global_id")),
            origin_created_at=item.get("origin_created_at"),
        )
    )


class LostOrganizationResponseClient:
    """Inject a response loss only after the real organization POST succeeded."""

    def __init__(self, client: GiteaProvisioningClient) -> None:
        self.client = client
        self.authenticated_login = client.authenticated_login

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def create_organization(self, username: str, visibility: str) -> dict[str, Any]:
        self.client.create_organization(username, visibility)
        raise OutcomeUnknownError("synthetic response loss after accepted organization POST")


class BundlePusher:
    """Import a verified bundle and push it without putting the password in argv."""

    def __init__(self, state: StateStore) -> None:
        self.state = state
        self.refs: dict[str, str] = {}

    def push(
        self,
        repository: str,
        *,
        bundle_member: str,
        bundle_sha256: str,
        bundle_size: int,
        expected_repository_id: str,
    ) -> TargetConfirmation:
        work_root = self.state.path.parent / "bundle-work"
        work_root.mkdir(mode=0o700, exist_ok=True)
        destination_member = f"{os.getpid()}-{hashlib.sha256(repository.encode()).hexdigest()}.git"
        imported = import_git_bundle(
            PACKAGE, bundle_member, bundle_sha256, bundle_size, work_root, destination_member
        )
        username = os.environ.get("GITFERRY_IT_ADMIN_USERNAME", TARGET_USER)
        password = _required(
            os.environ.get("GITFERRY_IT_ADMIN_PASSWORD"), "GITFERRY_IT_ADMIN_PASSWORD"
        )
        async_url = f"{TARGET_URL.rstrip('/')}/api/v1/repos/{repository}"
        response = httpx.get(async_url, auth=(username, password), timeout=20)
        response.raise_for_status()
        remote = response.json()
        if str(remote.get("id")) != expected_repository_id:
            raise RuntimeError("target repository id does not match the journal mapping")
        remote_url = f"{TARGET_URL.rstrip('/')}/{repository}.git"
        expected_refs = _local_refs(imported.destination)
        _git_remote("push", remote_url, imported.destination, username, password)
        refs = _git_remote("ls-remote", remote_url, None, username, password)
        if refs != expected_refs:
            raise RuntimeError("target repository has no refs after bundle push")
        self.refs = refs
        return TargetConfirmation(expected_repository_id, None)


def _git_remote(
    command: str, url: str, cwd: Path | None, username: str, password: str
) -> dict[str, str]:
    with tempfile.NamedTemporaryFile("w", prefix="gitferry-askpass-", delete=False) as script:
        script.write("#!/bin/sh\nprintf '%s\\n' \"$GITFERRY_ASKPASS_PASSWORD\"\n")
        script_path = Path(script.name)
    try:
        script_path.chmod(0o700)
        env = {
            **os.environ,
            "GIT_ASKPASS": str(script_path),
            "GIT_TERMINAL_PROMPT": "0",
            "GITFERRY_ASKPASS_PASSWORD": password,
        }
        args = [GIT, "-c", f"credential.username={username}", command]
        if command == "push":
            args.extend(["--mirror", url])
        else:
            args.append(url)
        result = subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True)
        return {
            ref: sha
            for sha, ref in (line.split("\t", 1) for line in result.stdout.splitlines())
            if ref != "HEAD"
        }
    finally:
        script_path.unlink(missing_ok=True)


def _local_refs(repository: Path) -> dict[str, str]:
    result = subprocess.run(
        [GIT, "show-ref"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return {ref: sha for sha, ref in (line.split(" ", 1) for line in result.stdout.splitlines())}


def _load_plan(path: Path) -> tuple[StateStore, ProvisioningPlan]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("plan must be a JSON object")
    state = StateStore(DATABASE)
    nonce = _required(os.environ.get("GITFERRY_IT_INSTANCE_NONCE"), "GITFERRY_IT_INSTANCE_NONCE")
    state.register_instance(
        SOURCE_INSTANCE, "Synthetic source Gitea", "http://source:3000", f"source-{nonce}"
    )
    state.register_instance(
        TARGET_INSTANCE, "Synthetic target Gitea", TARGET_URL, f"target-{nonce}"
    )
    state.register_realm("synthetic-directory-a", "Synthetic directory A", f"directory-a-{nonce}")
    for item in value.get("bindings", []):
        state.confirm_identity_binding(
            binding_id=str(_required(item.get("binding_id"), "binding_id")),
            source_realm_key=str(item.get("source_realm_key", "synthetic-directory-a")),
            source_login=str(_required(item.get("source_login"), "source_login")),
            source_external_id=str(_required(item.get("source_external_id"), "source_external_id")),
            source_ou_path=str(_required(item.get("source_ou_path"), "source_ou_path")),
            target_instance_key=TARGET_INSTANCE,
            target_account_id=int(_required(item.get("target_account_id"), "target_account_id")),
            target_login=str(_required(item.get("target_login"), "target_login")),
            mapping_version=int(item.get("mapping_version", 1)),
        )
    organization = _origin(
        state, value["organization"], "organization", str(value["organization"].get("name"))
    )
    repository = _origin(state, value["repository"], "repository", str(value["repository"]["name"]))
    teams: list[ProvisioningTeam] = []
    for item in value.get("teams", []):
        team_origin = _origin(state, item, "team", str(value["organization"]["name"]))
        name = str(_required(item.get("name"), "team.name"))
        teams.append(
            ProvisioningTeam(
                team_origin.id,
                name,
                str(item["permission"]),
                tuple(
                    ProvisioningMember(str(member["binding_id"]), str(member["role"]))
                    for member in item.get("members", [])
                ),
            )
        )
    bundle_value = value.get("bundle")
    bundle = (
        None
        if bundle_value is None
        else BundleIntent(
            str(bundle_value["member"]), str(bundle_value["sha256"]), int(bundle_value["size"])
        )
    )
    plan = ProvisioningPlan(
        TARGET_INSTANCE,
        organization.id,
        str(value["organization"]["name"]),
        repository.id,
        str(value["repository"]["name"]),
        tuple(teams),
        bundle,
    )
    return state, plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("apply", "crash-after-remote", "lost-response"))
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    state, plan = _load_plan(args.plan)
    username = os.environ.get("GITFERRY_IT_ADMIN_USERNAME", TARGET_USER)
    password = _required(os.environ.get("GITFERRY_IT_ADMIN_PASSWORD"), "GITFERRY_IT_ADMIN_PASSWORD")
    bundle_pusher = BundlePusher(state)
    with GiteaProvisioningClient(TARGET_URL, username=username, password=password) as raw:
        client = LostOrganizationResponseClient(raw) if args.mode == "lost-response" else raw
        hook = (lambda: os._exit(91)) if args.mode == "crash-after-remote" else None
        try:
            with OperationExecutor(
                state, f"provision-worker-{os.getpid()}", after_remote_hook=hook
            ) as executor:
                result = provision_selected_repository(
                    state, executor, client, plan, bundle_pusher=bundle_pusher
                )
        except OperationBlocked as exc:
            connection = sqlite3.connect(state.path)
            connection.row_factory = sqlite3.Row
            halted = [
                {
                    "operation_type": row["operation_type"],
                    "status": row["status"],
                    "reason": row["error_reason"],
                }
                for row in connection.execute(
                    "SELECT operation_type, status, error_reason FROM operations "
                    "WHERE status IN ('outcome_unknown', 'conflict', 'blocked_parent') "
                    "ORDER BY operation_type"
                )
            ]
            connection.close()
            print(
                json.dumps(
                    {"status": "blocked", "reason": str(exc), "halted": halted},
                    sort_keys=True,
                ),
                flush=True,
            )
            return 5
    print(
        json.dumps(
            {
                "status": "passed",
                "operations": [
                    {
                        "id": op.operation_id,
                        "status": op.status,
                        "target_global_id": op.target_global_id,
                    }
                    for op in result.operations
                ],
                "target_refs": bundle_pusher.refs,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
