from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from gitferry.native_operations import confirmed_native_facts, prepare_native_operations
from gitferry.operations import OperationBlocked
from gitferry.provisioning import ProvisioningPlan
from gitferry.state_store import OriginKey, StateStore, TargetConfirmation


def _setup(tmp_path: Path) -> tuple[StateStore, ProvisioningPlan]:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("source", "Source", "https://source.invalid", "install-source")
    state.register_instance("target", "Target", "https://target.invalid", "install-target")
    organization = state.register_origin(OriginKey("source", "org", "organization", "1"))
    repository = state.register_origin(OriginKey("source", "org/repo", "repository", "2"))
    plan = ProvisioningPlan("target", organization.id, "org", repository.id, "org/repo", (), None)
    return state, plan


def _selected(ref: str = "a" * 40) -> dict:
    return {
        "repository": "org/repo",
        "lfs": [{"oid": "a" * 64, "size": 4, "path": "lfs/" + "a" * 64}],
        "wiki": {
            "state": "populated",
            "refs": {"refs/heads/main": ref},
            "bundle": {"path": "wikis/repo.bundle", "size": 4, "sha256": "b" * 64},
        },
    }


def test_native_repackaging_reuses_lfs_and_wiki_operation_ids(tmp_path: Path) -> None:
    state, plan = _setup(tmp_path)
    first, first_dependencies = prepare_native_operations(state, plan, _selected())
    second, second_dependencies = prepare_native_operations(state, plan, deepcopy(_selected()))
    assert [item.operation_id for item in second] == [item.operation_id for item in first]
    assert second_dependencies == first_dependencies
    assert {item.operation_type for item in first} == {
        "upload_lfs_object",
        "bootstrap_wiki",
        "push_wiki_bundle",
    }


def test_changed_wiki_refs_block_without_creating_a_new_push(tmp_path: Path) -> None:
    state, plan = _setup(tmp_path)
    prepare_native_operations(state, plan, _selected())
    with pytest.raises(OperationBlocked, match="return baseline"):
        prepare_native_operations(state, plan, _selected(ref="c" * 40))


def test_outcome_unknown_same_wiki_intent_stays_unknown_across_new_package(tmp_path: Path) -> None:
    state, plan = _setup(tmp_path)
    operations, _ = prepare_native_operations(state, plan, _selected())
    push = next(item for item in operations if item.operation_type == "push_wiki_bundle")
    state.start_executor_session("executor")
    state.claim_operation(push.operation_id, "executor")
    state.mark_outcome_unknown(push.operation_id, "synthetic lost response")
    state.end_executor_session("executor")
    replay, _ = prepare_native_operations(state, plan, deepcopy(_selected()))
    replay_push = next(item for item in replay if item.operation_type == "push_wiki_bundle")
    assert replay_push.status == "outcome_unknown"


def test_confirmed_native_facts_use_durable_confirmed_event_and_unknown_has_none(
    tmp_path: Path,
) -> None:
    state, plan = _setup(tmp_path)
    operations, _ = prepare_native_operations(state, plan, _selected())
    bootstrap = next(item for item in operations if item.operation_type == "bootstrap_wiki")
    with pytest.raises(OperationBlocked):
        confirmed_native_facts(state, bootstrap)
    state.start_executor_session("executor")
    state.claim_operation(bootstrap.operation_id, "executor")
    state.add_object_mapping(
        bootstrap.origin_id,
        "target",
        "org/repo",
        "target-1",
    )
    facts = {"commit_sha": "c" * 40}
    state.confirm_operation(
        bootstrap.operation_id,
        TargetConfirmation("target-1", None, observed_state=facts),
    )
    state.end_executor_session("executor")
    assert confirmed_native_facts(state, state.get_operation(bootstrap.operation_id)) == facts
