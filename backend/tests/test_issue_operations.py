from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from gitferry.issue_operations import (
    execute_issue_operation,
    preflight_issue_operation,
    prepare_issue_operation,
)
from gitferry.operations import OperationBlocked, OperationExecutor
from gitferry.state_store import OperationConflict, OriginKey, StateStore

AUTHOR = {
    "realm_key": "realm-a",
    "login": "author-a",
    "external_id": "external-a",
    "ou_path": "OU=People",
}
VALUES = {
    "title": "Synthetic issue",
    "body": "Unicode ü\n**Markdown**",
    "state": "open",
    "labels": [],
}


class Issues:
    authenticated_login = "author-b"

    def __init__(self) -> None:
        self.objects: dict[int, dict[str, Any]] = {}
        self.posts = 0
        self.patches = 0
        self.lose_response = False

    def get_issue(self, repository: str, issue_index: int) -> dict[str, Any]:
        assert repository == "org/repo"
        return deepcopy(self.objects[issue_index])

    def create_issue(self, repository: str, values: dict[str, Any]) -> dict[str, Any]:
        self.posts += 1
        value = {
            **deepcopy(values),
            "id": 101,
            "number": 7,
            "user": {"id": 52, "login": "author-b"},
            "created_at": "2026-01-01T00:00:00Z",
        }
        self.objects[7] = value
        if self.lose_response:
            raise RuntimeError("synthetic lost response")
        return deepcopy(value)

    def update_issue(
        self, repository: str, issue_index: int, values: dict[str, Any]
    ) -> dict[str, Any]:
        self.patches += 1
        self.objects[issue_index].update(deepcopy(values))
        return self.get_issue(repository, issue_index)


def configured(tmp_path: Path) -> tuple[StateStore, str]:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("a", "A", "https://a.invalid", "install-a")
    state.register_instance("b", "B", "https://b.invalid", "install-b")
    state.register_realm("realm-a", "Realm A", "directory-a")
    state.confirm_identity_binding(
        binding_id="author",
        source_realm_key="realm-a",
        source_login="author-a",
        source_external_id="external-a",
        source_ou_path="OU=People",
        target_instance_key="b",
        target_account_id=52,
        target_login="author-b",
        mapping_version=1,
    )
    origin = state.register_origin(OriginKey("a", "org/repo", "issue", "11", 3))
    return state, origin.id


def prepare(state: StateStore, origin: str, **changes: Any) -> Any:
    args = dict(
        origin_id=origin,
        target_instance_key="b",
        target_repository_scope="org/repo",
        operation_type="create_issue",
        values=VALUES,
        author=AUTHOR,
    )
    return prepare_issue_operation(state, **{**args, **changes})


def test_native_author_same_id_update_and_stale_baseline_blocks(tmp_path: Path) -> None:
    state, origin = configured(tmp_path)
    client = Issues()
    created = prepare(state, origin)
    with OperationExecutor(state, "create") as executor:
        result = execute_issue_operation(state, executor, client, created.operation_id)
    assert result.target_global_id == "101" and result.target_issue_index == 7
    state.create_transfer_round("round", "a", "b", "org/repo")
    state.create_baseline(
        baseline_id="base",
        transfer_round_id="round",
        origin_id=origin,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline=VALUES,
        desired=VALUES,
        observed=VALUES,
        origin_created_at="2020-01-01T00:00:00Z",
        target_native_created_at="2026-01-01T00:00:00Z",
    )
    update = prepare(
        state,
        origin,
        operation_type="update_issue",
        baseline_id="base",
        values={**VALUES, "body": "Changed on B"},
    )
    with OperationExecutor(state, "update") as executor:
        changed = execute_issue_operation(state, executor, client, update.operation_id)
        assert (
            execute_issue_operation(state, executor, client, update.operation_id).status
            == "confirmed"
        )
    assert changed.target_global_id == result.target_global_id
    assert client.posts == 1 and client.patches == 1
    assert client.objects[7]["user"] == {"id": 52, "login": "author-b"}
    assert client.objects[7]["created_at"] == "2026-01-01T00:00:00Z"
    stale = prepare(
        state,
        origin,
        operation_type="update_issue",
        baseline_id="base",
        values={**VALUES, "body": "Stale change"},
    )
    with OperationExecutor(state, "stale") as executor, pytest.raises(OperationBlocked):
        execute_issue_operation(state, executor, client, stale.operation_id)
    assert client.patches == 1 and state.get_operation(stale.operation_id).status == "conflict"


def test_missing_author_and_changed_unknown_create_do_not_send(tmp_path: Path) -> None:
    state, origin = configured(tmp_path)
    with pytest.raises(OperationBlocked):
        prepare(state, origin, author={**AUTHOR, "realm_key": "other"})
    client = Issues()
    client.lose_response = True
    operation = prepare(state, origin)
    with OperationExecutor(state, "first") as executor, pytest.raises(OperationBlocked):
        execute_issue_operation(state, executor, client, operation.operation_id)
    assert state.get_operation(operation.operation_id).status == "outcome_unknown"
    with pytest.raises(OperationConflict):
        prepare(state, origin, values={**VALUES, "body": "new package content"})
    with OperationExecutor(state, "retry") as executor, pytest.raises(OperationBlocked):
        execute_issue_operation(state, executor, client, operation.operation_id)
    assert client.posts == 1


def test_separate_label_operation_owns_confirmed_issue_label_preflight(tmp_path: Path) -> None:
    state, origin = configured(tmp_path)
    client = Issues()
    operation = prepare(state, origin)
    with OperationExecutor(state, "create") as executor:
        execute_issue_operation(state, executor, client, operation.operation_id)

    client.objects[7]["labels"] = [{"name": "portable-priority"}]
    with pytest.raises(OperationBlocked, match="selected issue fields"):
        preflight_issue_operation(state, client, operation.operation_id)

    preflight_issue_operation(state, client, operation.operation_id, check_labels=False)
