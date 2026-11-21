from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("service", ["source", "target"])
@pytest.mark.parametrize("result", [0, 2, "timeout"])
def test_queue_boundary_retains_success_failure_and_timeout_evidence(
    tmp_path, monkeypatch, result, service
):
    monkeypatch.setenv("GITFERRY_IT_ADMIN_PASSWORD", "synthetic-fixture-only")
    monkeypatch.setenv("GITFERRY_IT_USER_PASSWORD", "synthetic-fixture-only")
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "tests/integration"))
    probe = importlib.import_module("support")
    monkeypatch.setattr(probe, "WORK", tmp_path)
    calls = []

    def run(command, *, check, timeout):
        calls.append(command)
        assert check is False and timeout == 45
        assert command[-6] == service
        assert command[-5:] == ["gitea", "manager", "flush-queues", "--timeout", "30s"]
        if result == "timeout":
            raise probe.ProbeFailure("synthetic command timeout")
        return subprocess.CompletedProcess(command, result, "", "")

    monkeypatch.setattr(probe, "run", run)
    if result == 0:
        probe.flush_fixture_queues(service, "collision")
    else:
        with pytest.raises(probe.ProbeFailure):
            probe.flush_fixture_queues(service, "collision")
    assert len(calls) == 1
    record = json.loads((tmp_path / "fixture-queues-collision.json").read_text())
    assert record == {
        "service": service,
        "phase": "collision",
        "exit_code": None if result == "timeout" else result,
        "status": "passed" if result == 0 else "failed",
    }


@pytest.mark.parametrize("before,observed,after", [(1, 1, 1), (1, 1, 2), (1, 2, 2), (1, 2, 3)])
def test_inventory_counts_require_both_independent_snapshots(
    tmp_path, monkeypatch, before, observed, after
):
    monkeypatch.setenv("GITFERRY_IT_ADMIN_PASSWORD", "synthetic-fixture-only")
    monkeypatch.setenv("GITFERRY_IT_USER_PASSWORD", "synthetic-fixture-only")
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "tests/integration"))
    probe = importlib.import_module("content_inventory_probe")
    monkeypatch.setattr(probe, "WORK", tmp_path)
    values = [{"git_refs": count, "lfs": None} for count in (before, observed, after)]
    if before == observed == after:
        probe._assert_counts(*values)
    else:
        with pytest.raises(probe.ProbeFailure, match="inventory counts differ"):
            probe._assert_counts(*values)
    evidence = json.loads((tmp_path / "inventory-counts.json").read_text())
    assert evidence["expected"] == values[0]
    assert evidence["observed"] == values[1]
    assert evidence["independent_after"] == values[2]


@pytest.mark.parametrize("state", ["running", "zombie", "disappeared", "unreadable"])
def test_browser_termination_observes_single_proc_read(monkeypatch, state):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "tests/integration"))
    probe = importlib.import_module("browser_runtime")
    calls = []

    class ProcStatus:
        def read_text(self):
            calls.append("read")
            if state == "disappeared":
                raise FileNotFoundError("process exited during read")
            if state == "unreadable":
                raise PermissionError("cannot prove exit")
            return "123 (python) " + ("Z" if state == "zombie" else "S") + " 0 0"

        def exists(self):
            raise AssertionError("existence precheck reintroduces the race")

    if state == "unreadable":
        with pytest.raises(PermissionError):
            probe.process_exited(ProcStatus())
    else:
        assert probe.process_exited(ProcStatus()) is (state != "running")
    assert calls == ["read"]
