"""Journaled ordinary Git ref operations for the bounded return flow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from .git_refs import ref_confirmation
from .operations import OperationExecutor
from .state_store import OperationBlocked, OperationRecord, StateStore, TargetConfirmation


def _operation_id(
    *,
    origin_id: str,
    target_instance_key: str,
    repository_scope: str,
    ref: str,
    original_oid: str | None,
    desired_oid: str,
    ruleset_version: int,
) -> str:
    identity = {
        "origin_id": origin_id,
        "target_instance_key": target_instance_key,
        "repository_scope": repository_scope,
        "ref": ref,
        "original_oid": original_oid,
        "desired_oid": desired_oid,
        "ruleset_version": ruleset_version,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return f"push-git-ref-{digest}"


def prepare_git_ref_operation(
    state: StateStore,
    *,
    origin_id: str,
    target_instance_key: str,
    repository_scope: str,
    repository_id: str,
    ref: str,
    original_oid: str | None,
    desired_oid: str,
    bundle_member: str,
    bundle_sha256: str,
    bundle_size: int,
    ruleset_version: int = 1,
) -> OperationRecord:
    """Persist one deterministic ref update; package identity is not part of it."""

    operation_id = _operation_id(
        origin_id=origin_id,
        target_instance_key=target_instance_key,
        repository_scope=repository_scope,
        ref=ref,
        original_oid=original_oid,
        desired_oid=desired_oid,
        ruleset_version=ruleset_version,
    )
    intent = {
        "kind": "git-ref-return",
        "repository_id": repository_id,
        "repository_scope": repository_scope,
        "ref": ref,
        "old_oid": original_oid,
        "new_oid": desired_oid,
        "bundle": {
            "member": bundle_member,
            "sha256": bundle_sha256,
            "size": bundle_size,
        },
        "ruleset": "git-refs",
        "ruleset_version": ruleset_version,
    }
    return state.prepare_operation(
        operation_id=operation_id,
        origin_id=origin_id,
        target_instance_key=target_instance_key,
        target_repository_scope=repository_scope,
        operation_type="push_git_ref",
        mapping_version=1,
        baseline_id=None,
        intent=intent,
    )


def execute_git_ref_operation(
    state: StateStore,
    operation_id: str,
    remote_write: Callable[[dict[str, Any]], TargetConfirmation],
    *,
    executor_id: str = "git-return",
) -> OperationRecord:
    """Execute once through the durable executor; unknown results are never retried."""

    with OperationExecutor(state, executor_id) as executor:
        return executor.execute(operation_id, remote_write)


def reconcile_git_ref_operation(
    state: StateStore,
    operation_id: str,
    live_read: Callable[[], Mapping[str, Any]],
    *,
    evidence: str,
) -> OperationRecord:
    """Confirm an unknown push only from exact local target identity and ref evidence."""

    operation = state.get_operation(operation_id)
    if operation.status != "outcome_unknown":
        raise OperationBlocked("Git ref operation is not awaiting reconciliation")
    live = dict(live_read())
    intent = operation.intent
    if live.get("target_instance_key") != operation.target_instance_key:
        raise OperationBlocked("reconciliation target instance does not match operation")
    if live.get("repository_id") != intent["repository_id"]:
        raise OperationBlocked("reconciliation repository identity does not match operation")
    if live.get("repository_scope") != operation.target_repository_scope:
        raise OperationBlocked("reconciliation repository scope does not match operation")
    refs = live.get("refs")
    if not isinstance(refs, Mapping) or refs.get(intent["ref"]) != intent["new_oid"]:
        raise OperationBlocked("reconciliation ref does not match intended Git object")
    observed_refs = dict(refs)
    observed = {
        "ref": intent["ref"],
        "refs": observed_refs,
        "refs_confirmation": ref_confirmation(observed_refs),
    }
    return state.resolve_unknown_operation(
        operation_id,
        target_global_id=str(intent["repository_id"]),
        target_issue_index=None,
        observed_intent=intent,
        evidence=evidence,
        observed_state=observed,
    )


__all__ = [
    "execute_git_ref_operation",
    "prepare_git_ref_operation",
    "reconcile_git_ref_operation",
]
