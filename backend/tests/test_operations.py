from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from gitferry.operations import (
    ExecutorBusy,
    OperationBlocked,
    OperationExecutor,
    OutcomeUnknownError,
    TargetConfirmation,
)
from gitferry.state_store import MappingConflict, OriginKey, StateStore


def prepared_store(tmp_path: Path, operation_id: str = "op-create-17") -> StateStore:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("instance-a", "Gitea A", "http://a.invalid", "install-a")
    state.register_instance("instance-b", "Gitea B", "http://b.invalid", "install-b")
    origin = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    state.prepare_operation(
        operation_id=operation_id,
        origin_id=origin.id,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "Synthetic", "body": "Body"},
    )
    return state


def confirmation() -> TargetConfirmation:
    return TargetConfirmation(
        target_global_id="71",
        target_issue_index=8,
        target_native_created_at="2026-11-20T10:05:00Z",
    )


def test_confirmed_operation_is_not_posted_again(tmp_path: Path) -> None:
    state = prepared_store(tmp_path)
    calls = 0

    def remote_write(_: dict[str, Any]) -> TargetConfirmation:
        nonlocal calls
        calls += 1
        return confirmation()

    with OperationExecutor(state, "executor-first") as executor:
        first = executor.execute("op-create-17", remote_write)
    with OperationExecutor(state, "executor-restart") as executor:
        second = executor.execute("op-create-17", remote_write)

    assert first.status == "confirmed"
    assert second.status == "confirmed"
    assert calls == 1
    assert state.get_object_mapping(first.origin_id, "instance-b").target_global_id == "71"


def test_lost_response_is_persisted_unknown_and_never_retried(tmp_path: Path) -> None:
    state = prepared_store(tmp_path)
    calls = 0

    def lost_response(_: dict[str, Any]) -> TargetConfirmation:
        nonlocal calls
        calls += 1
        raise OutcomeUnknownError("remote accepted the request but response was lost")

    with OperationExecutor(state, "executor-first") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation required"):
            executor.execute("op-create-17", lost_response)

    with OperationExecutor(state, "executor-restart") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation required"):
            executor.execute("op-create-17", lost_response)

    assert calls == 1
    assert state.get_operation("op-create-17").status == "outcome_unknown"


def test_interrupted_executing_state_becomes_unknown_on_next_executor_session(
    tmp_path: Path,
) -> None:
    state = prepared_store(tmp_path)
    state.start_executor_session("crashed-process")
    state.claim_operation("op-create-17", "crashed-process")

    with OperationExecutor(state, "restarted-process"):
        pass

    record = state.get_operation("op-create-17")
    assert record.status == "outcome_unknown"
    assert "interrupted" in (record.error_reason or "")


def test_exception_after_remote_success_leaves_recoverable_lease(tmp_path: Path) -> None:
    state = prepared_store(tmp_path)

    def fail_after_remote() -> None:
        raise RuntimeError("synthetic failure after remote success")

    with pytest.raises(RuntimeError, match="after remote success"):
        with OperationExecutor(
            state, "failing-process", after_remote_hook=fail_after_remote
        ) as executor:
            executor.execute("op-create-17", lambda _: confirmation())

    assert state.get_operation("op-create-17").status == "executing"
    with OperationExecutor(state, "replacement-process"):
        pass
    assert state.get_operation("op-create-17").status == "outcome_unknown"


def test_mapping_and_confirmed_status_roll_back_together_on_fault(tmp_path: Path) -> None:
    state = prepared_store(tmp_path)
    state.start_executor_session("executor")
    state.claim_operation("op-create-17", "executor")

    def fail_before_commit() -> None:
        raise RuntimeError("synthetic local commit failure")

    with pytest.raises(RuntimeError, match="synthetic local commit failure"):
        state.confirm_operation("op-create-17", confirmation(), fail_before_commit)

    with pytest.raises(KeyError):
        state.get_object_mapping(state.get_operation("op-create-17").origin_id, "instance-b")
    assert state.get_operation("op-create-17").status == "executing"


def test_unknown_resolution_requires_stable_target_id_and_exact_intent(tmp_path: Path) -> None:
    state = prepared_store(tmp_path)
    state.start_executor_session("crashed")
    state.claim_operation("op-create-17", "crashed")
    state.start_executor_session("restart", recover_abandoned=True)

    with pytest.raises(ValueError, match="stable target global id"):
        state.resolve_unknown_operation(
            "op-create-17",
            target_global_id="",
            target_issue_index=8,
            observed_intent={"title": "Synthetic", "body": "Body"},
            evidence="operator fetched target by id",
        )
    with pytest.raises(MappingConflict, match="does not exactly match"):
        state.resolve_unknown_operation(
            "op-create-17",
            target_global_id="71",
            target_issue_index=8,
            observed_intent={"title": "Synthetic", "body": "similar"},
            evidence="operator fetched target by id",
        )

    resolved = state.resolve_unknown_operation(
        "op-create-17",
        target_global_id="71",
        target_issue_index=8,
        observed_intent={"title": "Synthetic", "body": "Body"},
        evidence="operator fetched target 71 from registered instance-b/org/repo",
    )

    assert resolved.status == "confirmed"
    assert resolved.target_global_id == "71"


def test_global_executor_lock_prevents_two_simultaneous_remote_writes(tmp_path: Path) -> None:
    state = prepared_store(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def remote_write(_: dict[str, Any]) -> TargetConfirmation:
        calls.append("post")
        entered.set()
        assert release.wait(timeout=5)
        return confirmation()

    errors: list[BaseException] = []

    def first_executor() -> None:
        try:
            with OperationExecutor(state, "executor-one") as executor:
                executor.execute("op-create-17", remote_write)
        except BaseException as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    thread = threading.Thread(target=first_executor)
    thread.start()
    assert entered.wait(timeout=5)

    with pytest.raises(ExecutorBusy):
        with OperationExecutor(state, "executor-two"):
            pass

    release.set()
    thread.join(timeout=5)
    assert not errors
    assert calls == ["post"]


def test_confirmed_update_reuses_exact_existing_replica_mapping(tmp_path: Path) -> None:
    state = prepared_store(tmp_path, operation_id="op-create-initial")
    origin_id = state.get_operation("op-create-initial").origin_id
    state.add_object_mapping(
        origin_id,
        "instance-b",
        "org/repo",
        "71",
        target_issue_index=8,
        target_native_created_at="2026-11-20T10:05:00Z",
    )
    state.prepare_operation(
        operation_id="op-update-17",
        origin_id=origin_id,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="update_issue",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "Synthetic", "body": "Changed"},
    )

    with OperationExecutor(state, "update-executor") as executor:
        result = executor.execute("op-update-17", lambda _: confirmation())

    assert result.status == "confirmed"
    assert state.get_object_mapping(origin_id, "instance-b").target_global_id == "71"
