"""Reusable preparation, execution, and reconciliation for issue comments."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from gitferry.gitea_comments import (
    CommentTargetConflict,
    confirmation_from_comment,
    verify_comment_facts,
)
from gitferry.operations import OperationExecutor
from gitferry.state_store import (
    IdentityBindingRecord,
    OperationBlocked,
    OperationRecord,
    StateStore,
)


class CommentClient(Protocol):
    authenticated_login: str

    def verified_comment(
        self, repository: str, issue_index: int, comment_global_id: str
    ) -> dict[str, Any]: ...

    def create_comment(self, repository: str, issue_index: int, body: str) -> dict[str, Any]: ...

    def update_comment(
        self, repository: str, comment_global_id: str, body: str
    ) -> dict[str, Any]: ...


def comment_operation_id(origin_id: str, operation_type: str, intent: Mapping[str, Any]) -> str:
    from gitferry.state_codec import canonical_json

    digest = hashlib.sha256(canonical_json(intent).encode()).hexdigest()[:24]
    return f"{operation_type}-{origin_id[-16:]}-{digest}"


def _binding_for_author(
    state: StateStore, author: Mapping[str, Any], target_instance_key: str
) -> IdentityBindingRecord:
    try:
        return state.find_identity_binding(
            source_realm_key=str(author["realm_key"]),
            source_login=str(author["login"]),
            source_external_id=str(author["external_id"]),
            source_ou_path=str(author["ou_path"]),
            target_instance_key=target_instance_key,
        )
    except (KeyError, IndexError) as exc:
        raise OperationBlocked(
            "comment author has no exact locally confirmed identity binding"
        ) from exc


def prepare_comment_operation(
    state: StateStore,
    *,
    origin_id: str,
    target_instance_key: str,
    target_repository_scope: str,
    operation_type: str,
    body: str,
    author: Mapping[str, Any],
    baseline_id: str | None = None,
    expected_body: str | None = None,
) -> OperationRecord:
    if operation_type not in ("create_comment", "update_comment"):
        raise ValueError("unsupported comment operation type")
    origin = state.get_origin(origin_id)
    if origin.key.object_type != "comment" or origin.key.parent_origin_id is None:
        raise OperationBlocked("comment origin has no immutable parent issue origin")
    binding = _binding_for_author(state, author, target_instance_key)
    intent: dict[str, Any] = {
        "body": body,
        "parent_origin_id": origin.key.parent_origin_id,
        "author_binding_id": binding.binding_id,
    }
    if expected_body is not None:
        intent["expected_body"] = expected_body
    operation_id = comment_operation_id(origin_id, operation_type, intent)
    operation = state.prepare_operation(
        operation_id=operation_id,
        origin_id=origin_id,
        target_instance_key=target_instance_key,
        target_repository_scope=target_repository_scope,
        operation_type=operation_type,
        mapping_version=binding.mapping_version,
        baseline_id=baseline_id,
        intent=intent,
    )
    try:
        parent = state.get_object_mapping(origin.key.parent_origin_id, target_instance_key)
        if parent.target_issue_index is None:
            raise KeyError("parent target has no issue index")
    except KeyError:
        if operation.status == "prepared":
            return state.block_operation_on_parent(
                operation_id, "parent issue has no confirmed target mapping"
            )
        return operation
    if operation.status == "blocked_parent":
        return state.unblock_parent_operation(operation_id, origin.key.parent_origin_id)
    return operation


def execute_comment_operation(
    state: StateStore,
    executor: OperationExecutor,
    client: CommentClient,
    operation_id: str,
) -> OperationRecord:
    operation = state.get_operation(operation_id)
    if operation.status == "confirmed":
        return operation
    if operation.status == "blocked_parent":
        parent_id = str(operation.intent["parent_origin_id"])
        operation = state.unblock_parent_operation(operation_id, parent_id)
    binding = state.get_identity_binding(str(operation.intent["author_binding_id"]))
    if binding.target_instance_key != operation.target_instance_key:
        raise OperationBlocked("comment author binding points at another target instance")
    if client.authenticated_login != binding.target_login:
        raise OperationBlocked("comment write client is not the explicitly mapped target author")
    parent = state.get_object_mapping(
        str(operation.intent["parent_origin_id"]), operation.target_instance_key
    )
    if (
        parent.target_repository_scope != operation.target_repository_scope
        or parent.target_issue_index is None
    ):
        raise OperationBlocked("parent mapping does not match the local target repository")
    issue_index = parent.target_issue_index
    body = str(operation.intent["body"])

    if operation.operation_type == "update_comment":
        mapping = state.get_object_mapping(operation.origin_id, operation.target_instance_key)
        if (
            mapping.target_repository_scope != operation.target_repository_scope
            or mapping.target_issue_index != issue_index
        ):
            return state.mark_operation_conflict(
                operation_id,
                conflict_type="comment_parent_mismatch",
                reason="mapped comment does not point at the expected parent issue",
                evidence={"expected_issue_index": issue_index},
            )
        try:
            current = client.verified_comment(
                operation.target_repository_scope, issue_index, mapping.target_global_id
            )
            verify_comment_facts(
                current,
                issue_index=issue_index,
                expected_body=str(operation.intent["expected_body"]),
                expected_author_login=binding.target_login,
            )
        except CommentTargetConflict as exc:
            return state.mark_operation_conflict(
                operation_id,
                conflict_type="comment_target_drift",
                reason=str(exc),
                evidence={
                    "target_global_id": mapping.target_global_id,
                    "expected_issue_index": issue_index,
                },
            )

        def update(_: dict[str, Any]) -> Any:
            changed = client.update_comment(
                operation.target_repository_scope, mapping.target_global_id, body
            )
            verify_comment_facts(
                changed,
                issue_index=issue_index,
                expected_body=body,
                expected_author_login=binding.target_login,
            )
            return confirmation_from_comment(changed, issue_index)

        return executor.execute(operation_id, update)

    def create(_: dict[str, Any]) -> Any:
        created = client.create_comment(operation.target_repository_scope, issue_index, body)
        verify_comment_facts(
            created,
            issue_index=issue_index,
            expected_body=body,
            expected_author_login=binding.target_login,
        )
        return confirmation_from_comment(created, issue_index)

    return executor.execute(operation_id, create)


def resolve_unknown_comment_operation(
    state: StateStore,
    client: CommentClient,
    operation_id: str,
    *,
    target_comment_id: str,
) -> OperationRecord:
    operation = state.get_operation(operation_id)
    if operation.status != "outcome_unknown" or operation.operation_type != "create_comment":
        raise OperationBlocked("comment operation is not an unknown create awaiting resolution")
    binding = state.get_identity_binding(str(operation.intent["author_binding_id"]))
    if client.authenticated_login != binding.target_login:
        raise OperationBlocked("reconciliation client is not the mapped comment author")
    parent = state.get_object_mapping(
        str(operation.intent["parent_origin_id"]), operation.target_instance_key
    )
    if parent.target_issue_index is None:
        raise OperationBlocked("expected parent issue has no target mapping")
    comment = client.verified_comment(
        operation.target_repository_scope, parent.target_issue_index, target_comment_id
    )
    verify_comment_facts(
        comment,
        issue_index=parent.target_issue_index,
        expected_body=str(operation.intent["body"]),
        expected_author_login=binding.target_login,
    )
    return state.resolve_unknown_operation(
        operation_id,
        target_global_id=target_comment_id,
        target_issue_index=parent.target_issue_index,
        observed_intent=operation.intent,
        evidence="operator-selected comment verified through complete expected-issue listing",
    )
