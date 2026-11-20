"""Seed and verify synthetic durable state inside the packaged OCI runtime."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from gitferry.state_store import OriginKey, StateStore

DATABASE = Path("/var/lib/gitferry/state/gitferry.sqlite3")
PACKAGE_MARKER = Path("/var/lib/gitferry/packages/restart-proof.txt")
WORK_MARKER = Path("/var/lib/gitferry/work/restart-proof.txt")


def seed() -> dict[str, Any]:
    state = StateStore(DATABASE)
    state.register_instance(
        "oci-instance-a", "Synthetic A", "https://a.example.invalid", "oci-install-a"
    )
    state.register_instance(
        "oci-instance-b", "Synthetic B", "https://b.example.invalid", "oci-install-b"
    )
    state.register_realm("oci-realm-a", "Synthetic realm A", "oci-realm-marker-a")
    state.confirm_identity_binding(
        binding_id="oci-binding-a-b",
        source_realm_key="oci-realm-a",
        source_login="same-login",
        source_external_id="S-1-5-21-OCI-A-1001",
        source_ou_path="OU=People,DC=oci-a,DC=invalid",
        target_instance_key="oci-instance-b",
        target_account_id=4101,
        target_login="mapped-target-user",
        mapping_version=1,
    )
    origin = state.register_origin(
        OriginKey(
            "oci-instance-a",
            "synthetic/selected",
            "issue",
            "9001",
            issue_index=17,
            origin_created_at="2026-11-20T00:00:00Z",
        )
    )
    state.add_object_mapping(
        origin.id,
        "oci-instance-b",
        "synthetic/selected-copy",
        "12001",
        target_issue_index=23,
        target_native_created_at="2026-11-20T00:01:00Z",
    )
    state.create_transfer_round(
        "oci-round-1", "oci-instance-a", "oci-instance-b", "synthetic/selected"
    )
    state.create_baseline(
        baseline_id="oci-baseline-1",
        transfer_round_id="oci-round-1",
        origin_id=origin.id,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline={"title": "Before", "body": "Synthetic"},
        desired={"title": "After", "body": "Synthetic"},
        observed={"title": "Before", "body": "Synthetic"},
        origin_created_at="2026-11-20T00:00:00Z",
        target_native_created_at="2026-11-20T00:01:00Z",
    )
    state.prepare_operation(
        operation_id="oci-operation-unknown",
        origin_id=origin.id,
        target_instance_key="oci-instance-b",
        target_repository_scope="synthetic/selected-copy",
        operation_type="update_issue",
        mapping_version=1,
        baseline_id="oci-baseline-1",
        intent={"title": "After", "body": "Synthetic"},
    )
    state.start_executor_session("oci-executor")
    state.claim_operation("oci-operation-unknown", "oci-executor")
    state.mark_outcome_unknown(
        "oci-operation-unknown", "synthetic remote result requires reconciliation"
    )
    state.end_executor_session("oci-executor")
    PACKAGE_MARKER.write_text("persistent package marker\n", encoding="utf-8")
    WORK_MARKER.write_text("persistent work marker\n", encoding="utf-8")
    return snapshot(state)


def snapshot(state: StateStore) -> dict[str, Any]:
    binding = state.get_identity_binding("oci-binding-a-b")
    origin = state.register_origin(
        OriginKey(
            "oci-instance-a",
            "synthetic/selected",
            "issue",
            "9001",
            issue_index=17,
            origin_created_at="2026-11-20T00:00:00Z",
        )
    )
    mapping = state.get_object_mapping(origin.id, "oci-instance-b")
    baseline = state.get_baseline("oci-baseline-1")
    operation = state.get_operation("oci-operation-unknown")
    evidence = state.operation_evidence(operation.operation_id)
    connection = sqlite3.connect(DATABASE)
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    table_counts = {
        table: int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        )
        for table in (
            "identity_bindings",
            "object_mappings",
            "baselines",
            "operations",
            "operation_events",
            "conflicts",
        )
    }
    connection.close()
    return {
        "binding_id": binding.binding_id,
        "mapping_target_global_id": mapping.target_global_id,
        "baseline": dict(baseline.baseline),
        "operation_status": operation.status,
        "event_transitions": [event["to"] for event in evidence["events"]],
        "open_conflict_types": [
            conflict["type"] for conflict in evidence["conflicts"] if conflict["status"] == "open"
        ],
        "integrity_check": integrity,
        "table_counts": table_counts,
        "package_marker": PACKAGE_MARKER.read_text(encoding="utf-8"),
        "work_marker": WORK_MARKER.read_text(encoding="utf-8"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seed", "verify"))
    args = parser.parse_args()
    result = seed() if args.mode == "seed" else snapshot(StateStore(DATABASE))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
