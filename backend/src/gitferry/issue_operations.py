"""Issue operations using the same local bindings, baselines and journal as comments."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from gitferry.baselines import issue_baseline
from gitferry.comment_operations import resolve_author_binding
from gitferry.operations import OperationExecutor
from gitferry.state_codec import canonical_json
from gitferry.state_store import OperationBlocked, OperationRecord, StateStore, TargetConfirmation


class IssueClient(Protocol):
    authenticated_login: str

    def get_issue(self, repository: str, issue_index: int) -> dict[str, Any]: ...
    def create_issue(self, repository: str, values: Mapping[str, Any]) -> dict[str, Any]: ...
    def update_issue(
        self, repository: str, issue_index: int, values: Mapping[str, Any]
    ) -> dict[str, Any]: ...


def issue_values(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize only the previously checked fields, never native IDs/timestamps."""
    facts: dict[str, Any] = dict(issue_baseline(value).values)
    facts["labels"] = list(facts["labels"])
    return facts


def validate_issue_values(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"title", "body", "state", "labels"}:
        raise ValueError("issue fields must be title, body, state and labels")
    labels = value["labels"]
    if not isinstance(labels, list) or any(not isinstance(name, str) for name in labels):
        raise ValueError("issue labels must be names")
    if len(set(labels)) != len(labels):
        raise ValueError("issue label names must be unique")
    facts = issue_values({**value, "labels": [{"name": name} for name in labels]})
    if not facts["title"] or facts["state"] not in ("open", "closed"):
        raise ValueError("invalid issue title or state")
    return facts


def prepare_issue_operation(
    state: StateStore,
    *,
    origin_id: str,
    target_instance_key: str,
    target_repository_scope: str,
    operation_type: str,
    values: Mapping[str, Any],
    author: Mapping[str, Any],
    baseline_id: str | None = None,
) -> OperationRecord:
    if operation_type not in ("create_issue", "update_issue"):
        raise ValueError("unsupported issue operation")
    if state.get_origin(origin_id).key.object_type != "issue":
        raise OperationBlocked("issue operation requires an issue origin")
    binding = resolve_author_binding(state, author, target_instance_key)
    intent = {"values": validate_issue_values(values), "author_binding_id": binding.binding_id}
    if operation_type == "update_issue":
        if baseline_id is None:
            raise OperationBlocked("issue updates require an explicit immutable baseline")
        baseline = state.get_baseline(baseline_id)
        if (
            baseline.origin_id != origin_id
            or baseline.ruleset_version != 1
            or baseline.ruleset_name != "gitea-issue-selected-fields"
        ):
            raise OperationBlocked("baseline is not for this issue and comparison version")
        intent["expected"] = validate_issue_values(baseline.baseline)
    # A create is one semantic operation per origin+target. Changed content cannot
    # bypass an earlier uncertain create by producing a fresh journal entry.
    identity: dict[str, Any] = {
        "origin": origin_id,
        "target": target_instance_key,
        "kind": operation_type,
    }
    if operation_type == "update_issue":
        identity.update(intent=intent, baseline_id=baseline_id)
    identifier = (
        operation_type + "-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    )
    return state.prepare_operation(
        operation_id=identifier,
        origin_id=origin_id,
        target_instance_key=target_instance_key,
        target_repository_scope=target_repository_scope,
        operation_type=operation_type,
        mapping_version=binding.mapping_version,
        baseline_id=baseline_id,
        intent=intent,
    )


def verify_issue(
    value: Mapping[str, Any],
    *,
    account_id: int,
    login: str,
    expected: Mapping[str, Any],
    global_id: str | None = None,
    issue_index: int | None = None,
    created_at: str | None = None,
) -> TargetConfirmation:
    user = value.get("user")
    if (
        not isinstance(user, Mapping)
        or str(user.get("id")) != str(account_id)
        or user.get("login") != login
    ):
        raise OperationBlocked("native issue author does not match the local identity binding")
    identifier, index = value.get("id"), value.get("number")
    if type(identifier) is not int or identifier <= 0 or type(index) is not int or index <= 0:
        raise OperationBlocked("issue response has no positive global ID and repository index")
    if (global_id is not None and str(identifier) != global_id) or (
        issue_index is not None and index != issue_index
    ):
        raise OperationBlocked("mapped issue identity changed")
    if issue_values(value) != validate_issue_values(expected):
        raise OperationBlocked("selected issue fields differ from the expected state")
    native_created_at = value.get("created_at")
    if not isinstance(native_created_at, str) or (
        created_at is not None and created_at != native_created_at
    ):
        raise OperationBlocked("native issue creation time is missing or changed")
    return TargetConfirmation(str(identifier), index, native_created_at)


def preflight_issue_operation(
    state: StateStore,
    client: IssueClient,
    operation_id: str,
    *,
    check_labels: bool = True,
) -> None:
    operation = state.get_operation(operation_id)
    if operation.status in ("outcome_unknown", "conflict", "failed", "executing"):
        raise OperationBlocked("issue operation requires reconciliation")
    if (
        operation.operation_type == "update_issue"
        and operation.intent["values"]["labels"] != operation.intent["expected"]["labels"]
    ):
        raise OperationBlocked("issue label changes require a separate supported operation")
    binding = state.get_identity_binding(str(operation.intent["author_binding_id"]))
    if (
        binding.target_instance_key != operation.target_instance_key
        or binding.target_login != client.authenticated_login
    ):
        raise OperationBlocked("issue write client does not match the explicit target author")
    try:
        mapping = state.get_object_mapping(operation.origin_id, operation.target_instance_key)
    except KeyError:
        if operation.operation_type == "create_issue" and operation.status != "confirmed":
            return
        raise OperationBlocked("mapped issue is missing") from None
    if operation.operation_type == "create_issue" and operation.status != "confirmed":
        raise OperationBlocked("an existing issue copy cannot be created again")
    if (
        mapping.target_repository_scope != operation.target_repository_scope
        or mapping.target_issue_index is None
    ):
        raise OperationBlocked("issue mapping points at another repository")
    expected = (
        operation.intent["values"]
        if operation.status == "confirmed"
        else operation.intent["expected"]
    )
    value = client.get_issue(operation.target_repository_scope, mapping.target_issue_index)
    if not check_labels:
        expected = {**expected, "labels": issue_values(value)["labels"]}
    try:
        verify_issue(
            value,
            account_id=binding.target_account_id,
            login=binding.target_login,
            expected=expected,
            global_id=mapping.target_global_id,
            issue_index=mapping.target_issue_index,
            created_at=mapping.target_native_created_at,
        )
    except OperationBlocked as exc:
        if operation.status != "confirmed":
            state.mark_operation_conflict(
                operation_id,
                conflict_type="issue_target_drift",
                reason=str(exc),
                evidence={"target_global_id": mapping.target_global_id},
            )
        raise


def execute_issue_operation(
    state: StateStore, executor: OperationExecutor, client: IssueClient, operation_id: str
) -> OperationRecord:
    operation = state.get_operation(operation_id)
    if operation.status == "confirmed":
        return operation
    preflight_issue_operation(state, client, operation_id)
    binding = state.get_identity_binding(str(operation.intent["author_binding_id"]))

    def write(_: dict[str, Any]) -> TargetConfirmation:
        expected = operation.intent["values"]
        if operation.operation_type == "create_issue":
            value = client.create_issue(operation.target_repository_scope, expected)
            return verify_issue(
                value,
                account_id=binding.target_account_id,
                login=binding.target_login,
                expected=expected,
            )
        mapping = state.get_object_mapping(operation.origin_id, operation.target_instance_key)
        if mapping.target_issue_index is None:
            raise OperationBlocked("issue mapping has no repository index")
        value = client.update_issue(
            operation.target_repository_scope, mapping.target_issue_index, expected
        )
        return verify_issue(
            value,
            account_id=binding.target_account_id,
            login=binding.target_login,
            expected=expected,
            global_id=mapping.target_global_id,
            issue_index=mapping.target_issue_index,
            created_at=mapping.target_native_created_at,
        )

    return executor.execute(operation_id, write)
