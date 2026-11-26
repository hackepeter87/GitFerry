from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from gitferry.state_store import OriginKey, StateStore, TargetConfirmation


def prepared(tmp_path):
    state = StateStore(tmp_path / "state.sqlite3")
    for name in ("a", "b"):
        state.register_instance(name, name, f"https://{name}.invalid", name + "-installation")
    origin = state.register_origin(OriginKey("a", "org/repo", "repository", "7"))
    state.prepare_operation(
        operation_id="create",
        origin_id=origin.id,
        target_instance_key="b",
        target_repository_scope="org/repo",
        operation_type="create_repository",
        mapping_version=1,
        baseline_id=None,
        intent={"name": "repo"},
    )
    state.start_executor_session("executor")
    state.claim_operation("create", "executor")
    return state, origin


def test_confirmation_facts_survive_real_process_restart(tmp_path):
    state, _ = prepared(tmp_path)
    facts = {"refs": {"refs/heads/master": "a" * 40}}
    state.confirm_operation("create", TargetConfirmation("19", None, observed_state=facts))
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            (
                "import json,sys;from gitferry.state_store import StateStore;"
                "print(json.dumps(StateStore(sys.argv[1]).operation_evidence('create')))"
            ),
            str(state.path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )  # noqa: S603
    assert json.loads(result.stdout)["events"][-1]["evidence"]["observed_state"] == facts


def test_confirmation_failure_is_atomic_with_mapping_and_event(tmp_path):
    state, _ = prepared(tmp_path)

    def fail():
        raise RuntimeError("synthetic before commit")

    with pytest.raises(RuntimeError):
        state.confirm_operation(
            "create",
            TargetConfirmation("19", None, observed_state={"commit": "a" * 40}),
            before_commit=fail,
        )
    assert state.get_operation("create").status == "executing"
    assert not any(e["to"] == "confirmed" for e in state.operation_evidence("create")["events"])
    with state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM object_mappings").fetchone()[0] == 0


@pytest.mark.parametrize(
    "facts",
    [
        {"password": "secret"},
        {"nested": {"authorization": "hidden"}},
        {"bad": float("nan")},
        {"large": "x" * 300000},
    ],
)
def test_invalid_or_secret_confirmation_does_not_commit(tmp_path, facts):
    state, _ = prepared(tmp_path)
    with pytest.raises(ValueError):
        state.confirm_operation("create", TargetConfirmation("19", None, observed_state=facts))
    assert state.get_operation("create").status == "executing"


def test_auxiliary_confirmation_preserves_repository_mapping(tmp_path):
    state, origin = prepared(tmp_path)
    state.confirm_operation("create", TargetConfirmation("19", None))
    state.prepare_operation(
        operation_id="wiki",
        origin_id=origin.id,
        target_instance_key="b",
        target_repository_scope="org/repo",
        operation_type="bootstrap_wiki",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "Synthetic"},
    )
    state.claim_operation("wiki", "executor")
    state.mark_outcome_unknown("wiki", "synthetic response lost")
    state.resolve_unknown_operation(
        "wiki",
        target_global_id="19",
        target_issue_index=None,
        observed_intent={"title": "Synthetic"},
        evidence="explicit verified native response",
        observed_state={"commit": "b" * 40},
    )
    assert state.get_object_mapping(origin.id, "b").target_global_id == "19"
    assert state.operation_evidence("wiki")["events"][-1]["evidence"]["observed_state"] == {
        "commit": "b" * 40
    }


def test_schema_16_upgrade_retains_unknown_operation_and_events(tmp_path):
    database = tmp_path / "old.sqlite3"
    script = """
import sys
import gitferry.state_store as module
from gitferry.state_store import StateStore, OriginKey
module.SCHEMA_VERSION = 16
state=StateStore(sys.argv[1])
for name in ('a','b'):
    state.register_instance(name,name,'https://'+name+'.invalid',name+'-installation')
origin=state.register_origin(OriginKey('a','org/repo','repository','7'))
state.prepare_operation(operation_id='unknown',origin_id=origin.id,target_instance_key='b',
    target_repository_scope='org/repo',operation_type='create_repository',mapping_version=1,
    baseline_id=None,intent={'name':'repo'})
state.start_executor_session('old')
state.claim_operation('unknown','old')
state.mark_outcome_unknown('unknown','synthetic lost response')
"""
    subprocess.run([sys.executable, "-c", script, str(database)], check=True)  # noqa: S603
    upgraded = StateStore(database)
    assert upgraded.get_operation("unknown").status == "outcome_unknown"
    assert upgraded.operation_evidence("unknown")["events"][-1]["to"] == "outcome_unknown"
    with upgraded.read_connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not list(connection.execute("PRAGMA foreign_key_check"))
