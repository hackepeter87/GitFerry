"""One-process worker used to prove durable restart behavior against real Gitea."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from gitferry.baselines import IssueBaseline, issue_baseline, issue_has_drift
from gitferry.operations import (
    ExecutorBusy,
    OperationBlocked,
    OperationExecutor,
    OutcomeUnknownError,
    TargetConfirmation,
)
from gitferry.state_store import OriginKey, StateStore
from permissions_probe import SELECTED_REPO
from support import SOURCE_URL, TARGET_URL, USER_PASSWORD, api, request

INSTANCE_A = "gitea-a-integration"
INSTANCE_B = "gitea-b-integration"
REALM_A = "directory-a-integration"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "mode",
        choices=("seed-contracts", "verify-contracts", "normal", "crash", "lost", "status"),
    )
    result.add_argument("--database", type=Path, required=True)
    result.add_argument("--operation-id")
    result.add_argument("--origin-global-id")
    result.add_argument("--origin-issue-index", type=int)
    result.add_argument("--target-account-id", type=int)
    result.add_argument("--executor-id", default=f"worker-{os.getpid()}")
    result.add_argument("--ready-file", type=Path)
    result.add_argument("--hold-seconds", type=float, default=0)
    return result


def initialize_state(database: Path) -> StateStore:
    state = StateStore(database)
    state.register_instance(INSTANCE_A, "Gitea A", SOURCE_URL, "compose-source-volume-2026-11-20")
    state.register_instance(INSTANCE_B, "Gitea B", TARGET_URL, "compose-target-volume-2026-11-20")
    return state


def require_origin(args: argparse.Namespace, state: StateStore) -> Any:
    if args.origin_global_id is None or args.origin_issue_index is None:
        raise ValueError("origin id and issue index are required")
    return state.register_origin(
        OriginKey(
            INSTANCE_A,
            SELECTED_REPO,
            "issue",
            args.origin_global_id,
            issue_index=args.origin_issue_index,
        )
    )


def seed_contracts(args: argparse.Namespace, state: StateStore) -> dict[str, Any]:
    if args.target_account_id is None:
        raise ValueError("target account id is required")
    origin = require_origin(args, state)
    state.register_realm(REALM_A, "Synthetic directory A", "forest-a-integration")
    state.confirm_identity_binding(
        binding_id="binding-issue-author-a-to-b",
        source_realm_key=REALM_A,
        source_login="issue-author",
        source_external_id="S-1-5-21-A-2001",
        source_ou_path="OU=People,DC=a,DC=invalid",
        target_instance_key=INSTANCE_B,
        target_account_id=args.target_account_id,
        target_login="issue-author",
        mapping_version=1,
    )
    source_issue = api(
        SOURCE_URL,
        "GET",
        f"/repos/{SELECTED_REPO}/issues/{args.origin_issue_index}",
        auth=("issue-author", USER_PASSWORD),
    )
    baseline = issue_baseline(source_issue)
    state.create_transfer_round("restart-round-1", INSTANCE_A, INSTANCE_B, SELECTED_REPO)
    state.create_baseline(
        baseline_id="restart-baseline-1",
        transfer_round_id="restart-round-1",
        origin_id=origin.id,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline=dict(baseline.values),
        desired=dict(baseline.values),
        observed=dict(baseline.values),
        origin_created_at=source_issue["created_at"],
        target_native_created_at=None,
    )
    return {"status": "passed", "origin_id": origin.id, "baseline": dict(baseline.values)}


def verify_contracts(args: argparse.Namespace, state: StateStore) -> dict[str, Any]:
    if args.origin_issue_index is None:
        raise ValueError("origin issue index is required")
    binding = state.get_identity_binding("binding-issue-author-a-to-b")
    stored = state.get_baseline("restart-baseline-1")
    values = dict(stored.baseline)
    values["labels"] = tuple(values["labels"])
    baseline = IssueBaseline(fields=("title", "body", "state", "labels"), values=values)
    source_issue = api(
        SOURCE_URL,
        "GET",
        f"/repos/{SELECTED_REPO}/issues/{args.origin_issue_index}",
        auth=("issue-author", USER_PASSWORD),
    )
    drift = issue_has_drift(baseline, source_issue)
    observed = issue_baseline(source_issue)
    if drift:
        state.record_conflict(
            "drift-restart-baseline-1",
            operation_id=None,
            conflict_type="selected_data_drift",
            reason="selected issue fields differ from the immutable transfer baseline",
            evidence={"baseline": stored.baseline, "observed": dict(observed.values)},
        )
    state.update_baseline_observation("restart-baseline-1", dict(observed.values))
    reread = state.get_baseline("restart-baseline-1")
    return {
        "status": "passed",
        "binding_target_account_id": binding.target_account_id,
        "baseline": reread.baseline,
        "observed": reread.observed,
        "observation_revision": reread.observation_revision,
        "drift": drift,
    }


def prepare_operation(args: argparse.Namespace, state: StateStore) -> None:
    if args.operation_id is None:
        raise ValueError("operation id is required")
    origin = require_origin(args, state)
    state.prepare_operation(
        operation_id=args.operation_id,
        origin_id=origin.id,
        target_instance_key=INSTANCE_B,
        target_repository_scope=SELECTED_REPO,
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent={
            "title": f"Restart proof {args.operation_id}",
            "body": "Synthetic durable operation probe",
        },
    )


def remote_create(args: argparse.Namespace, intent: dict[str, Any]) -> TargetConfirmation:
    if args.ready_file is not None:
        args.ready_file.write_text("writer owns lock", encoding="utf-8")
    if args.hold_seconds:
        time.sleep(args.hold_seconds)
    response = request(
        TARGET_URL,
        "POST",
        f"/repos/{SELECTED_REPO}/issues",
        auth=("issue-author", USER_PASSWORD),
        json_body=intent,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Gitea rejected create with {response.status_code}: {response.text}")
    target = response.json()
    if args.operation_id is None:
        raise ValueError("operation id is required for remote acceptance evidence")
    evidence_path = args.database.parent / f"{args.operation_id}.remote-accepted.json"
    temporary = evidence_path.with_suffix(f".json.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "remote_accepted": True,
                "target_global_id": str(target["id"]),
                "target_issue_index": int(target["number"]),
                "title": target["title"],
                "body": target["body"],
                "author": target["user"]["login"],
            },
            handle,
            sort_keys=True,
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, evidence_path)
    if args.mode == "lost":
        raise OutcomeUnknownError("successful response deliberately discarded by failure point")
    return TargetConfirmation(
        target_global_id=str(target["id"]),
        target_issue_index=int(target["number"]),
        target_native_created_at=str(target["created_at"]),
    )


def run_operation(args: argparse.Namespace, state: StateStore) -> tuple[int, dict[str, Any]]:
    prepare_operation(args, state)
    if args.mode == "status":
        with OperationExecutor(state, args.executor_id):
            operation = state.get_operation(args.operation_id)
        return 0, {
            "status": operation.status,
            "target_global_id": operation.target_global_id,
            "reason": operation.error_reason,
        }

    hook = (lambda: os._exit(91)) if args.mode == "crash" else None
    try:
        with OperationExecutor(state, args.executor_id, after_remote_hook=hook) as executor:
            operation = executor.execute(
                args.operation_id, lambda intent: remote_create(args, intent)
            )
        return 0, {
            "status": operation.status,
            "target_global_id": operation.target_global_id,
            "target_issue_index": operation.target_issue_index,
        }
    except OperationBlocked as exc:
        operation = state.get_operation(args.operation_id)
        return 3, {"status": operation.status, "reason": str(exc)}
    except ExecutorBusy as exc:
        return 4, {"status": "blocked", "reason": str(exc)}


def main() -> int:
    args = parser().parse_args()
    state = initialize_state(args.database)
    if args.mode == "seed-contracts":
        output = seed_contracts(args, state)
        exit_code = 0
    elif args.mode == "verify-contracts":
        output = verify_contracts(args, state)
        exit_code = 0
    else:
        exit_code, output = run_operation(args, state)
    print(json.dumps(output, sort_keys=True), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
