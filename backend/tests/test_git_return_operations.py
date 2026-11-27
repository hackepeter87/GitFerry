from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gitferry.git_return_operations import (
    execute_git_ref_operation,
    prepare_git_ref_operation,
    reconcile_git_ref_operation,
)
from gitferry.operations import OperationBlocked, OutcomeUnknownError, TargetConfirmation
from gitferry.state_store import MappingConflict, OriginKey, StateStore


def setup_state(tmp_path: Path) -> tuple[StateStore, str]:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("a", "A", "https://a.invalid", "a-install")
    state.register_instance("b", "B", "https://b.invalid", "b-install")
    origin = state.register_origin(OriginKey("a", "org/repo", "repository", "41"))
    state.add_object_mapping(origin.id, "a", "org/repo", "41")
    return state, origin.id


def prepare(state: StateStore, origin_id: str):
    return prepare_git_ref_operation(
        state,
        origin_id=origin_id,
        target_instance_key="a",
        repository_scope="org/repo",
        repository_id="41",
        ref="refs/heads/main",
        original_oid="a" * 40,
        desired_oid="b" * 40,
        bundle_member="bundles/repo.bundle",
        bundle_sha256="c" * 64,
        bundle_size=4,
    )


def test_repackaging_uses_same_operation_and_changed_intent_conflicts(tmp_path: Path) -> None:
    state, origin = setup_state(tmp_path)
    first = prepare(state, origin)
    second = prepare(state, origin)
    assert first.operation_id == second.operation_id
    with pytest.raises(Exception, match="different intent"):
        state.prepare_operation(
            operation_id=first.operation_id,
            origin_id=origin,
            target_instance_key="a",
            target_repository_scope="org/repo",
            operation_type="push_git_ref",
            mapping_version=1,
            baseline_id=None,
            intent={"ref": "refs/heads/other"},
        )


def test_confirmed_replay_does_not_call_remote_again(tmp_path: Path) -> None:
    state, origin = setup_state(tmp_path)
    operation = prepare(state, origin)
    calls = 0

    def remote(_: dict[str, Any]) -> TargetConfirmation:
        nonlocal calls
        calls += 1
        return TargetConfirmation(
            "41", None, observed_state={"refs": {"refs/heads/main": "b" * 40}}
        )

    assert execute_git_ref_operation(state, operation.operation_id, remote).status == "confirmed"
    assert execute_git_ref_operation(state, operation.operation_id, remote).status == "confirmed"
    assert calls == 1


def test_lost_response_stays_unknown_and_reconciliation_requires_exact_target(
    tmp_path: Path,
) -> None:
    state, origin = setup_state(tmp_path)
    operation = prepare(state, origin)

    def lost(_: dict[str, Any]) -> TargetConfirmation:
        raise OutcomeUnknownError("response lost")

    with pytest.raises(OperationBlocked):
        execute_git_ref_operation(state, operation.operation_id, lost)
    with pytest.raises(OperationBlocked, match="repository identity"):
        reconcile_git_ref_operation(
            state,
            operation.operation_id,
            lambda: {
                "target_instance_key": "a",
                "repository_scope": "org/repo",
                "repository_id": "wrong",
                "refs": {"refs/heads/main": "b" * 40},
            },
            evidence="synthetic",
        )
    with pytest.raises(OperationBlocked, match="ref"):
        reconcile_git_ref_operation(
            state,
            operation.operation_id,
            lambda: {
                "target_instance_key": "a",
                "repository_scope": "org/repo",
                "repository_id": "41",
                "refs": {"refs/heads/main": "d" * 40},
            },
            evidence="synthetic",
        )
    confirmed = reconcile_git_ref_operation(
        state,
        operation.operation_id,
        lambda: {
            "target_instance_key": "a",
            "repository_scope": "org/repo",
            "repository_id": "41",
            "refs": {"refs/heads/main": "b" * 40},
        },
        evidence="synthetic exact remote ref proof",
    )
    assert confirmed.status == "confirmed"


def test_unknown_reconciliation_does_not_accept_changed_intent(tmp_path: Path) -> None:
    state, origin = setup_state(tmp_path)
    operation = prepare(state, origin)
    with pytest.raises(OperationBlocked):
        execute_git_ref_operation(
            state,
            operation.operation_id,
            lambda _: (_ for _ in ()).throw(OutcomeUnknownError("lost")),
        )
    with pytest.raises(MappingConflict):
        state.resolve_unknown_operation(
            operation.operation_id,
            target_global_id="41",
            target_issue_index=None,
            observed_intent={**operation.intent, "new_oid": "d" * 40},
            evidence="forged",
        )
