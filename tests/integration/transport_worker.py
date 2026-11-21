"""Side-local worker for the isolated portable A -> B -> A integration proof."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from gitferry.operations import OperationExecutor, TargetConfirmation
from gitferry.state_store import OriginKey, StateStore
from gitferry.transport import load_transport, write_transport
from gitferry.transport_state import (
    admit_transport,
    confirm_import_observation,
    get_import_observation,
    record_export,
)

INSTANCE_A = "gitea-a-portable"
INSTANCE_B = "gitea-b-portable"
REPOSITORY = "access-lab/selected-private"
DATABASE = Path("/state/gitferry.sqlite3")
FORWARD_IN = Path("/state/inbox/forward")
RETURN_IN = Path(os.environ.get("GITFERRY_RETURN_PATH", "/state/inbox/return"))
FORWARD_OUT = Path("/state/outbox/forward")
RETURN_OUT = Path("/state/outbox/return")
USER = "issue-author"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "seed-forward",
            "import-forward",
            "modify-and-return",
            "repackage-return",
            "check-return",
            "apply-return",
        ),
    )
    return parser.parse_args()


def marker(instance: str) -> str:
    nonce = os.environ["GITFERRY_IT_INSTANCE_NONCE"]
    return f"{instance}-{nonce}"


def state_store() -> StateStore:
    state = StateStore(DATABASE)
    state.register_instance(INSTANCE_A, "Gitea A", "http://source:3000", marker("source"))
    state.register_instance(INSTANCE_B, "Gitea B", "http://target:3000", marker("target"))
    return state


def request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = httpx.request(
        method,
        os.environ["GITFERRY_GITEA_URL"] + "/api/v1" + path,
        auth=(USER, os.environ["GITFERRY_IT_USER_PASSWORD"]),
        json=body,
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Gitea {method} {path} returned {response.status_code}: {response.text}"
        )
    return response.json()


def issue_values(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": issue["title"],
        "body": issue["body"],
        "state": issue["state"],
        "labels": sorted(label["name"] for label in issue["labels"]),
    }


def selection() -> list[dict[str, Any]]:
    return [
        {
            "repository": REPOSITORY,
            "object_types": ["issue"],
            "fields": ["title", "body", "state", "labels"],
            "capabilities": ["issue-create", "issue-update"],
            "limitations": [
                "native-created-at-not-restored",
                "v1-contract-carries-issues-only",
            ],
        }
    ]


def replica(instance_key: str, mapping: Any) -> dict[str, Any]:
    result = {
        "instance_key": instance_key,
        "repository": mapping.target_repository_scope,
        "global_id": mapping.target_global_id,
        "issue_index": str(mapping.target_issue_index),
    }
    if mapping.target_native_created_at:
        result["native_created_at"] = mapping.target_native_created_at
    return result


def create_remote(values: dict[str, Any]) -> TargetConfirmation:
    target = request(
        "POST",
        f"/repos/{REPOSITORY}/issues",
        {
            "title": values["title"],
            "body": values["body"],
            "closed": values["state"] == "closed",
            "labels": [],
        },
    )
    return TargetConfirmation(str(target["id"]), int(target["number"]), target["created_at"])


def update_remote(issue_index: int, values: dict[str, Any]) -> TargetConfirmation:
    current = request("GET", f"/repos/{REPOSITORY}/issues/{issue_index}")
    target = request(
        "PATCH",
        f"/repos/{REPOSITORY}/issues/{issue_index}",
        {
            "title": values["title"],
            "body": values["body"],
            "state": values["state"],
            "labels": [],
            "content_version": current["content_version"],
        },
    )
    return TargetConfirmation(str(target["id"]), int(target["number"]), target["created_at"])


def seed_forward(state: StateStore) -> dict[str, Any]:
    issue = request(
        "POST",
        f"/repos/{REPOSITORY}/issues",
        {"title": "Portable origin issue", "body": "Original content on A"},
    )
    origin = state.register_origin(
        OriginKey(
            INSTANCE_A,
            REPOSITORY,
            "issue",
            str(issue["id"]),
            issue_index=int(issue["number"]),
            origin_created_at=issue["created_at"],
        )
    )
    state.add_object_mapping(
        origin.id,
        INSTANCE_A,
        REPOSITORY,
        str(issue["id"]),
        target_issue_index=int(issue["number"]),
        target_native_created_at=issue["created_at"],
    )
    values = issue_values(issue)
    state.create_transfer_round("round-portable-1", INSTANCE_A, INSTANCE_B, REPOSITORY)
    state.create_baseline(
        baseline_id="portable-a-baseline",
        transfer_round_id="round-portable-1",
        origin_id=origin.id,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline=values,
        desired=values,
        observed=values,
        origin_created_at=issue["created_at"],
        target_native_created_at=issue["created_at"],
    )
    user = request("GET", f"/users/{USER}")
    document = {
        "format": "gitferry.transport",
        "schema_version": 1,
        "manifest_id": "portable-forward-1",
        "transfer_round_id": "round-portable-1",
        "producer_version": "0.1.0.dev0",
        "sender": {"instance_key": INSTANCE_A, "installation_marker": marker("source")},
        "recipient": {
            "instance_key": INSTANCE_B,
            "installation_marker": marker("target"),
            "context": "isolated GitFerry B state",
        },
        "mode": "forward",
        "created_at": datetime.now(UTC).isoformat(),
        "deletion_semantics": "absence-does-not-delete",
        "selected_repositories": selection(),
        "identity_claims": [
            {
                "realm_key": "synthetic-directory-a",
                "login": USER,
                "external_id": "SYNTHETIC-A-2001",
                "ou_path": "OU=People,DC=a,DC=invalid",
                "relationship": {
                    "instance_key": INSTANCE_A,
                    "account_id": str(user["id"]),
                    "login": USER,
                    "status": "observed-not-approved",
                },
            }
        ],
        "objects": [
            {
                "origin": {
                    "instance_key": INSTANCE_A,
                    "repository": REPOSITORY,
                    "object_type": "issue",
                    "global_id": str(issue["id"]),
                    "issue_index": str(issue["number"]),
                    "created_at": issue["created_at"],
                },
                "replicas": [replica(INSTANCE_A, state.get_object_mapping(origin.id, INSTANCE_A))],
                "comparison": {
                    "ruleset": "gitea-issue-selected-fields",
                    "ruleset_version": 1,
                    "original_baseline": values,
                    "confirmed_import": None,
                    "current": values,
                    "baseline_origin": {
                        "instance_key": INSTANCE_A,
                        "transfer_round_id": "round-portable-1",
                    },
                },
            }
        ],
        "payloads": [],
    }
    artifact = write_transport(FORWARD_OUT, document)
    report = record_export(state, artifact, local_instance_key=INSTANCE_A)
    return {
        "status": report.status,
        "manifest_id": report.manifest_id,
        "digest_sha256": report.digest_sha256,
        "origin_id": origin.id,
        "issue": issue,
    }


def import_forward(state: StateStore) -> dict[str, Any]:
    artifact = load_transport(FORWARD_IN)
    report = admit_transport(state, artifact, local_instance_key=INSTANCE_B)
    origin_identifier = report.origin_ids[0]
    values = artifact.document["objects"][0]["comparison"]["current"]
    state.prepare_operation(
        operation_id=f"portable-forward-create-{origin_identifier}",
        origin_id=origin_identifier,
        target_instance_key=INSTANCE_B,
        target_repository_scope=REPOSITORY,
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent=values,
    )
    with OperationExecutor(state, f"forward-{os.getpid()}") as executor:
        operation = executor.execute(f"portable-forward-create-{origin_identifier}", create_remote)
    target = request("GET", f"/repos/{REPOSITORY}/issues/{operation.target_issue_index}")
    confirm_import_observation(
        state,
        forward_manifest_id=artifact.document["manifest_id"],
        origin_identifier=origin_identifier,
        observed=issue_values(target),
        target_native_created_at=target["created_at"],
    )
    return {
        "status": operation.status,
        "admission": report.status,
        "origin_id": origin_identifier,
        "issue": target,
    }


def modify_and_return(state: StateStore, manifest_id: str = "portable-return-1") -> dict[str, Any]:
    forward = load_transport(FORWARD_IN)
    origin_identifier, forward_item = next(
        (identifier, item)
        for identifier, item in (
            (
                state.register_origin(
                    OriginKey(
                        value["instance_key"],
                        value["repository"],
                        "issue",
                        value["global_id"],
                        issue_index=int(value["issue_index"]),
                        origin_created_at=value.get("created_at"),
                    )
                ).id,
                item,
            )
            for item in forward.document["objects"]
            for value in (item["origin"],)
        )
    )
    b_mapping = state.get_object_mapping(origin_identifier, INSTANCE_B)
    changed = update_remote(
        int(b_mapping.target_issue_index),
        {
            **forward_item["comparison"]["current"],
            "body": "Changed on B and returned through files",
        },
    )
    changed = request("GET", f"/repos/{REPOSITORY}/issues/{changed.target_issue_index}")
    new_issue = request(
        "POST",
        f"/repos/{REPOSITORY}/issues",
        {"title": "New issue born on B", "body": "B-origin content"},
    )
    new_origin = state.register_origin(
        OriginKey(
            INSTANCE_B,
            REPOSITORY,
            "issue",
            str(new_issue["id"]),
            issue_index=int(new_issue["number"]),
            origin_created_at=new_issue["created_at"],
        )
    )
    state.add_object_mapping(
        new_origin.id,
        INSTANCE_B,
        REPOSITORY,
        str(new_issue["id"]),
        target_issue_index=int(new_issue["number"]),
        target_native_created_at=new_issue["created_at"],
    )
    document = {
        **forward.document,
        "manifest_id": manifest_id,
        "sender": {"instance_key": INSTANCE_B, "installation_marker": marker("target")},
        "recipient": {
            "instance_key": INSTANCE_A,
            "installation_marker": marker("source"),
            "context": "isolated GitFerry A state",
        },
        "mode": "return",
        "created_at": datetime.now(UTC).isoformat(),
        "forward_reference": {
            "manifest_id": forward.document["manifest_id"],
            "digest_sha256": forward.digest_sha256,
        },
        "objects": [
            {
                **forward_item,
                "replicas": [
                    *forward_item["replicas"],
                    replica(INSTANCE_B, b_mapping),
                ],
                "comparison": {
                    **forward_item["comparison"],
                    "confirmed_import": get_import_observation(
                        state,
                        forward_manifest_id=forward.document["manifest_id"],
                        origin_identifier=origin_identifier,
                    ),
                    "current": issue_values(changed),
                },
            },
            {
                "origin": {
                    "instance_key": INSTANCE_B,
                    "repository": REPOSITORY,
                    "object_type": "issue",
                    "global_id": str(new_issue["id"]),
                    "issue_index": str(new_issue["number"]),
                    "created_at": new_issue["created_at"],
                },
                "replicas": [
                    replica(INSTANCE_B, state.get_object_mapping(new_origin.id, INSTANCE_B))
                ],
                "comparison": {
                    "ruleset": "gitea-issue-selected-fields",
                    "ruleset_version": 1,
                    "original_baseline": None,
                    "confirmed_import": None,
                    "current": issue_values(new_issue),
                    "baseline_origin": {
                        "instance_key": INSTANCE_B,
                        "transfer_round_id": "round-portable-1",
                    },
                },
            },
        ],
    }
    output = RETURN_OUT if manifest_id == "portable-return-1" else Path("/state/outbox/repackaged")
    artifact = write_transport(output, document)
    report = record_export(state, artifact, local_instance_key=INSTANCE_B)
    return {
        "status": report.status,
        "manifest_id": report.manifest_id,
        "digest_sha256": report.digest_sha256,
        "changed_issue": changed,
        "new_issue": new_issue,
        "origin_ids": list(report.origin_ids),
    }


def repackage_return(state: StateStore) -> dict[str, Any]:
    original = load_transport(RETURN_OUT)
    document = {
        **original.document,
        "manifest_id": "portable-return-repackaged",
        "created_at": datetime.now(UTC).isoformat(),
    }
    artifact = write_transport(Path("/state/outbox/repackaged"), document)
    report = record_export(state, artifact, local_instance_key=INSTANCE_B)
    return {
        "status": report.status,
        "manifest_id": report.manifest_id,
        "digest_sha256": report.digest_sha256,
        "origin_ids": list(report.origin_ids),
    }


def check_return(state: StateStore) -> tuple[bool, dict[str, Any]]:
    artifact = load_transport(RETURN_IN)
    report = admit_transport(state, artifact, local_instance_key=INSTANCE_A)
    drift: list[str] = []
    for item, identifier in zip(artifact.document["objects"], report.origin_ids, strict=False):
        baseline = item["comparison"]["original_baseline"]
        if item["origin"]["instance_key"] != INSTANCE_A or baseline is None:
            continue
        mapping = state.get_object_mapping(identifier, INSTANCE_A)
        current = request("GET", f"/repos/{REPOSITORY}/issues/{mapping.target_issue_index}")
        operation_id = f"portable-return-update-{identifier}"
        try:
            operation = state.get_operation(operation_id)
        except KeyError:
            expected = baseline
        else:
            expected = (
                item["comparison"]["current"] if operation.status == "confirmed" else baseline
            )
        if issue_values(current) != expected:
            drift.append(identifier)
    return not drift, {
        "status": "ready" if not drift else "blocked",
        "admission": report.status,
        "drift_origins": drift,
        "origin_ids": list(report.origin_ids),
    }


def apply_return(state: StateStore) -> tuple[int, dict[str, Any]]:
    ready, check = check_return(state)
    if not ready:
        return 5, check
    artifact = load_transport(RETURN_IN)
    outcomes: list[dict[str, Any]] = []
    for item, identifier in zip(artifact.document["objects"], check["origin_ids"], strict=False):
        values = item["comparison"]["current"]
        if item["origin"]["instance_key"] == INSTANCE_A:
            mapping = state.get_object_mapping(identifier, INSTANCE_A)
            operation_type = "update_issue"
            operation_id = f"portable-return-update-{identifier}"
            issue_index = int(mapping.target_issue_index)

            def remote(intent: dict[str, Any], index: int = issue_index) -> TargetConfirmation:
                return update_remote(index, intent)

            baseline_id = "portable-a-baseline"
        else:
            operation_type = "create_issue"
            operation_id = f"portable-return-create-{identifier}"
            remote = create_remote
            baseline_id = None
        state.prepare_operation(
            operation_id=operation_id,
            origin_id=identifier,
            target_instance_key=INSTANCE_A,
            target_repository_scope=REPOSITORY,
            operation_type=operation_type,
            mapping_version=1,
            baseline_id=baseline_id,
            intent=values,
        )
        with OperationExecutor(state, f"return-{os.getpid()}-{len(outcomes)}") as executor:
            result = executor.execute(operation_id, remote)
        target = request("GET", f"/repos/{REPOSITORY}/issues/{result.target_issue_index}")
        outcomes.append(
            {
                "operation_id": operation_id,
                "status": result.status,
                "target_global_id": result.target_global_id,
                "target_issue_index": result.target_issue_index,
                "author": target["user"]["login"],
                "values": issue_values(target),
                "native_created_at": target["created_at"],
                "origin_created_at": item["origin"].get("created_at"),
            }
        )
    return 0, {"status": "passed", "admission": check["admission"], "outcomes": outcomes}


def main() -> int:
    args = parse_args()
    side = os.environ["GITFERRY_SIDE"]
    if args.mode in ("seed-forward", "check-return", "apply-return") and side != "A":
        raise RuntimeError("A-only mode invoked in another local context")
    if args.mode in ("import-forward", "modify-and-return", "repackage-return") and side != "B":
        raise RuntimeError("B-only mode invoked in another local context")
    state = state_store()
    if args.mode == "seed-forward":
        code, output = 0, seed_forward(state)
    elif args.mode == "import-forward":
        code, output = 0, import_forward(state)
    elif args.mode == "modify-and-return":
        code, output = 0, modify_and_return(state)
    elif args.mode == "repackage-return":
        code, output = 0, repackage_return(state)
    elif args.mode == "check-return":
        ready, output = check_return(state)
        code = 0 if ready else 5
    else:
        code, output = apply_return(state)
    print(json.dumps(output, sort_keys=True), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
