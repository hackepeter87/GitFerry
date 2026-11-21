from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from gitferry.directory_execution import (
    DirectoryBootstrapConflict,
    DirectoryBootstrapService,
    DirectoryBootstrapUnknown,
)
from gitferry.operations import ExecutorBusy
from gitferry.state_store import StateStore


def service(tmp_path: Path) -> DirectoryBootstrapService:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("instance-a", "Synthetic A", "http://a.invalid", "install-a")
    state.register_realm("realm-a", "Synthetic LDAP", "realm-marker-a")
    return DirectoryBootstrapService(state)


def prepared(tmp_path: Path):
    svc = service(tmp_path)
    return svc, svc.prepare("instance-a", "realm-a", "ldap", "a" * 64, "b" * 64)


def test_prepare_is_idempotent_and_changed_intent_conflicts(tmp_path: Path) -> None:
    svc, first = prepared(tmp_path)
    assert svc.prepare("instance-a", "realm-a", "ldap", "a" * 64, "b" * 64) == first
    with pytest.raises(DirectoryBootstrapConflict, match="intent changed"):
        svc.prepare("instance-a", "realm-a", "ldap", "c" * 64, "b" * 64)
    assert svc.get("instance-a", "ldap").status == "conflict"
    assert svc.events("instance-a", "ldap")[-1]["to_status"] == "conflict"


def test_apply_confirms_decimal_native_id_and_replay_verifies_without_runner(
    tmp_path: Path,
) -> None:
    svc, record = prepared(tmp_path)
    calls = 0

    def runner(_):
        nonlocal calls
        calls += 1
        return "9007199254740993"

    confirmed = svc.apply(record, runner)
    replay = svc.apply(
        confirmed,
        lambda _: pytest.fail("must not rerun"),
        verify=lambda current: current.native_source_id == "9007199254740993",
    )
    assert confirmed.status == replay.status == "confirmed"
    assert confirmed.native_source_id == "9007199254740993"
    assert calls == 1


def test_runner_failure_is_unknown_and_never_retried(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    calls = 0

    def runner(_):
        nonlocal calls
        calls += 1
        raise RuntimeError("sensitive remote detail")

    with pytest.raises(DirectoryBootstrapUnknown, match="outcome is unknown"):
        svc.apply(record, runner)
    with pytest.raises(DirectoryBootstrapUnknown):
        svc.apply(svc.get("instance-a", "ldap"), runner)
    assert calls == 1
    assert svc.get("instance-a", "ldap").reason == "directory bootstrap execution failed"
    assert "sensitive" not in str(svc.events("instance-a", "ldap"))


def test_interrupted_executing_state_becomes_unknown_without_callback(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    executing = svc._transition(record, "executing")
    with pytest.raises(DirectoryBootstrapUnknown, match="outcome is unknown"):
        svc.apply(executing, lambda _: pytest.fail("must not retry"))
    assert svc.get("instance-a", "ldap").status == "outcome_unknown"


def test_unknown_state_is_preserved_when_new_intent_is_prepared(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    unknown = svc._transition(record, "outcome_unknown")
    with pytest.raises(DirectoryBootstrapConflict, match="uncertainty"):
        svc.prepare("instance-a", "realm-a", "ldap", "c" * 64, "b" * 64)
    assert svc.get("instance-a", "ldap").status == unknown.status == "outcome_unknown"


def test_stale_record_cannot_apply_after_intent_changes(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    with pytest.raises(DirectoryBootstrapConflict):
        svc.prepare("instance-a", "realm-a", "ldap", "c" * 64, "b" * 64)
    with pytest.raises(DirectoryBootstrapConflict):
        svc.apply(record, lambda _: "1")


def test_changed_prepare_during_callback_cannot_overwrite_executing(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)

    def runner(_):
        with pytest.raises(DirectoryBootstrapConflict, match="uncertainty"):
            svc.prepare("instance-a", "realm-a", "ldap", "c" * 64, "b" * 64)
        assert svc.get("instance-a", "ldap").status == "executing"
        return "1"

    confirmed = svc.apply(record, runner)
    assert confirmed.status == "confirmed"


def test_before_execute_failure_leaves_prepared_and_can_retry(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    runner_calls = 0

    def runner(_):
        nonlocal runner_calls
        runner_calls += 1
        return "1"

    with pytest.raises(RuntimeError, match="prerequisite"):
        svc.apply(
            record,
            runner,
            before_execute=lambda: (_ for _ in ()).throw(RuntimeError("prerequisite")),
        )
    preserved = svc.get("instance-a", "ldap")
    assert preserved is not None and preserved.status == "prepared"
    assert [event["to_status"] for event in svc.events("instance-a", "ldap")] == ["prepared"]
    confirmed = svc.apply(record, runner, before_execute=lambda: None)
    assert confirmed.status == "confirmed"
    assert runner_calls == 1


def test_realm_key_accepts_registered_slash_identifier(tmp_path: Path) -> None:
    svc = service(tmp_path)
    state = svc.state
    state.register_realm("corp/example", "Synthetic Corp", "realm-marker-corp")
    record = svc.prepare("instance-a", "corp/example", "ldap", "a" * 64, "b" * 64)
    assert record.realm_key == "corp/example"


def test_confirmed_replay_requires_successful_readback(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    confirmed = svc.apply(record, lambda _: "1")
    with pytest.raises(DirectoryBootstrapConflict, match="requires readback"):
        svc.apply(confirmed, lambda _: "1", verify=None)
    with pytest.raises(DirectoryBootstrapConflict, match="changed"):
        svc.apply(confirmed, lambda _: "1", verify=lambda _: False)
    with pytest.raises(DirectoryBootstrapConflict, match="readback failed"):
        svc.apply(
            confirmed,
            lambda _: "1",
            verify=lambda _: (_ for _ in ()).throw(RuntimeError("secret detail")),
        )


def test_subprocess_restart_marks_durable_execution_unknown(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    child = (
        "from gitferry.directory_execution import DirectoryBootstrapService; "
        "from gitferry.state_store import StateStore; "
        "import sys; "
        "state = StateStore(sys.argv[1]); "
        "service = DirectoryBootstrapService(state); "
        "service._transition(service.get('instance-a', 'ldap'), 'executing')"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(  # noqa: S603 - fixed interpreter and inline test code
        [sys.executable, "-c", child, str(tmp_path / "state.sqlite3")],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    with pytest.raises(DirectoryBootstrapUnknown):
        svc.apply(record, lambda _: pytest.fail("must not retry"))
    assert svc.get("instance-a", "ldap").status == "outcome_unknown"


def test_invalid_native_id_is_unknown(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    with pytest.raises(DirectoryBootstrapUnknown):
        svc.apply(record, lambda _: "source-id-not-decimal")
    assert svc.get("instance-a", "ldap").status == "outcome_unknown"


def test_writer_lease_serializes_directory_callbacks(tmp_path: Path) -> None:
    svc, record = prepared(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def runner(_):
        entered.set()
        assert release.wait(5)
        return "1"

    def first():
        try:
            svc.apply(record, runner)
        except BaseException as exc:  # pragma: no cover - diagnostic only
            errors.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(5)
    with pytest.raises(ExecutorBusy):
        svc.apply(record, lambda _: "2")
    release.set()
    thread.join(5)
    assert not errors
