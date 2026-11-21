"""Native payload steps in the existing journal, scoped to a mapped repository."""

from __future__ import annotations

from typing import Any

from .provisioning import ProvisioningPlan, _prepare
from .state_store import OperationBlocked, OperationRecord, StateStore

NATIVE_OPERATION_TYPES = frozenset({"upload_lfs_object", "bootstrap_wiki", "push_wiki_bundle"})
_BOOTSTRAP = {
    "title": "GitFerryImportBootstrap",
    "content": "Technical bootstrap for the reviewed GitFerry wiki import.\n",
    "message": "Initialize native wiki transport",
    "contract": "gitea-wiki-bootstrap-v1",
}


def prepare_native_operations(
    state: StateStore, plan: ProvisioningPlan, selected: dict[str, Any]
) -> tuple[list[OperationRecord], dict[str, list[str]]]:
    """Called only after package validation; no remote calls or authority adoption."""
    if selected["repository"] != plan.repository:
        raise OperationBlocked("native payloads point at another selected repository")
    if state.get_origin(plan.repository_origin_id).key.object_type != "repository":
        raise OperationBlocked("native payload parent is not a repository origin")
    operations: list[OperationRecord] = []
    dependencies: dict[str, list[str]] = {}

    def prepare(kind: str, intent: dict[str, Any]) -> OperationRecord:
        operation = _prepare(
            state,
            plan,
            origin_id=plan.repository_origin_id,
            operation_type=kind,
            repository_scope=plan.repository,
            mapping_version=1,
            intent=intent,
        )
        operations.append(operation)
        dependencies[operation.operation_id] = []
        return operation

    for pointer in selected.get("lfs") or []:
        prepare(
            "upload_lfs_object",
            {
                "oid": pointer["oid"],
                "size": pointer["size"],
                "member": pointer["path"],
            },
        )
    wiki = selected.get("wiki")
    if wiki is not None and wiki["state"] == "populated":
        bootstrap = prepare("bootstrap_wiki", _BOOTSTRAP)
        bundle = wiki["bundle"]
        intent = {
            "member": bundle["path"],
            "sha256": bundle["sha256"],
            "size": bundle["size"],
            "refs": wiki["refs"],
            "bootstrap_operation_id": bootstrap.operation_id,
        }
        # Repackaging an identical native result does not invent a second effect.
        # A changed ref set needs a separate return/baseline contract, not first-import.
        with state.read_connection() as connection:
            previous = connection.execute(
                "SELECT operation_id FROM operations WHERE origin_id=? AND "
                "target_instance_key=? AND operation_type='push_wiki_bundle'",
                (plan.repository_origin_id, plan.target_instance_key),
            ).fetchall()
        if previous:
            if len(previous) != 1:
                raise OperationBlocked("wiki history contains conflicting first-import operations")
            operation = state.get_operation(previous[0][0])
            if operation.intent.get("refs") != wiki["refs"]:
                raise OperationBlocked("wiki ref changes require an explicit return baseline")
            if operation.intent != intent and operation.status not in (
                "confirmed",
                "outcome_unknown",
            ):
                raise OperationBlocked("unconfirmed wiki intent differs from the original payload")
            operations.append(operation)
        else:
            operation = prepare("push_wiki_bundle", intent)
        dependencies[operation.operation_id] = [bootstrap.operation_id]
    elif wiki is not None and wiki["state"] != "empty":
        raise OperationBlocked("disabled wiki configuration transfer is not supported")
    return operations, dependencies


def confirmed_native_facts(state: StateStore, operation: OperationRecord) -> dict[str, Any]:
    if operation.status != "confirmed":
        raise OperationBlocked("native operation has no confirmed result")
    events = state.operation_evidence(operation.operation_id)["events"]
    facts = [
        event["evidence"]["observed_state"]
        for event in events
        if event["to"] == "confirmed"
        and event.get("evidence")
        and isinstance(event["evidence"].get("observed_state"), dict)
    ]
    if len(facts) != 1:
        raise OperationBlocked("native operation has no unique durable confirmation")
    return dict(facts[0])
