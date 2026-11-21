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
