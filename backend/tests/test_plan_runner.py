from __future__ import annotations

import json
import subprocess
import sys
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from gitferry.operations import ExecutorBusy, OperationExecutor
from gitferry.plan_runner import PlanRunner
from gitferry.state_store import TargetConfirmation
from gitferry.transfer_plans import PlanService
from test_transfer_plans import _plan, _store


def test_crash_after_remote_then_restart_blocks_without_second_write(tmp_path: Path) -> None:
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = _plan(service, evidence)
    service.queue(plan["plan_id"], plan["digest"])
    script = """
import os, sys, json
from pathlib import Path
from contextlib import nullcontext
from gitferry.plan_runner import PlanRunner
from gitferry.transfer_plans import PlanService
from gitferry.state_store import StateStore, TargetConfirmation
service = PlanService(StateStore(sys.argv[1]))
remote = Path(sys.argv[3])
class Adapter:
    def preflight(self, plan): pass
    def apply(self, plan, executor):
        def post(intent):
            count = int(remote.read_text()) if remote.exists() else 0
            remote.write_text(str(count + 1))
            return TargetConfirmation(str(100 + count), 1, "2026-01-01T00:00:00Z")
        executor.execute("op-1", post)
def crash(): os._exit(91)
runner = PlanRunner(service,lambda _: nullcontext(Adapter()),
    after_remote_hook=crash if sys.argv[4]=="crash" else None)
print(json.dumps(runner.run(sys.argv[2])))
"""
    remote = tmp_path / "synthetic-remote-post-count"
    command = [sys.executable, "-c", script, str(store.path), plan["plan_id"], str(remote)]
    first = subprocess.run(  # noqa: S603 -- fixed synthetic child program
        command + ["crash"], capture_output=True, text=True, timeout=10
    )
    assert first.returncode == 91 and remote.read_text() == "1"
    assert store.get_operation("op-1").status == "executing"
    resumed = subprocess.run(  # noqa: S603 -- fixed synthetic child program
        command + ["resume"], check=True, capture_output=True, text=True, timeout=10
    )
    assert json.loads(resumed.stdout)["status"] == "blocked"
    assert store.get_operation("op-1").status == "outcome_unknown"
    assert remote.read_text() == "1"
    repackaged = _plan(service, evidence, round_key="another-round")
    service.queue(repackaged["plan_id"], repackaged["digest"])
    # No target ID is supplied to the recovering application by this harness.
    command[4] = repackaged["plan_id"]
    repeated = subprocess.run(  # noqa: S603 -- fixed synthetic child program
        command + ["resume"], check=True, capture_output=True, text=True, timeout=10
    )
    assert json.loads(repeated.stdout)["status"] == "blocked" and remote.read_text() == "1"


def test_preflight_failure_has_no_writes_and_single_executor(tmp_path: Path) -> None:
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = _plan(service, evidence)
    service.queue(plan["plan_id"], plan["digest"])
    entered, release = threading.Event(), threading.Event()
    writes: list[str] = []

    class Adapter:
        def preflight(self, plan: dict[str, Any]) -> None:
            entered.set()
            assert release.wait(timeout=5)
            raise ValueError("synthetic preflight rejection")

        def apply(self, plan: dict[str, Any], executor: OperationExecutor) -> None:
            writes.append("unexpected")

    runner = PlanRunner(service, lambda _: nullcontext(Adapter()))
    thread = threading.Thread(target=runner.run, args=(plan["plan_id"],))
    thread.start()
    try:
        assert entered.wait(timeout=5)
        with pytest.raises(ExecutorBusy):
            runner.run(plan["plan_id"])
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not writes
    assert service.get(plan["plan_id"])["status"] == "failed"
    assert store.get_operation("op-1").status == "prepared"


def test_lost_response_remains_unknown_and_cancel_stops_before_write(tmp_path: Path) -> None:
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = _plan(service, evidence)
    service.queue(plan["plan_id"], plan["digest"])
    writes: list[str] = []

    class Adapter:
        def preflight(self, plan: dict[str, Any]) -> None:
            pass

        def apply(self, plan: dict[str, Any], executor: OperationExecutor) -> None:
            def post(intent: dict[str, Any]) -> TargetConfirmation:
                writes.append("accepted")
                raise RuntimeError("synthetic response loss after acceptance")

            executor.execute("op-1", post)

    runner = PlanRunner(service, lambda _: nullcontext(Adapter()))
    assert runner.run(plan["plan_id"])["status"] == "blocked"
    assert writes == ["accepted"]
    assert store.get_operation("op-1").status == "outcome_unknown"
    new = _plan(service, evidence, round_key="cancel-round")
    service.queue(new["plan_id"], new["digest"])
    service.cancel(new["plan_id"])
    assert service.get(new["plan_id"])["status"] == "cancelled"
    assert runner.run_next() is None and writes == ["accepted"]
    old = store.get_operation("op-1")
    store.prepare_operation(
        operation_id="repackaged-operation",
        origin_id=old.origin_id,
        target_instance_key=old.target_instance_key,
        target_repository_scope=old.target_repository_scope,
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "changed"},
    )
    repackaged = _plan(
        service,
        evidence,
        round_key="changed-intent-round",
        operation_ids=["repackaged-operation"],
        dependencies={"repackaged-operation": []},
    )
    service.queue(repackaged["plan_id"], repackaged["digest"])
    assert runner.run(repackaged["plan_id"])["status"] == "blocked"
    assert store.get_operation("repackaged-operation").status == "prepared"
    assert writes == ["accepted"]
