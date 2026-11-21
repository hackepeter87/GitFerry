from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest


def worker(monkeypatch: pytest.MonkeyPatch) -> Any:
    directory = Path(__file__).parents[2] / "tests/integration"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "package_probe_readiness", directory / "package_admission_worker.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = [0.0]

    def advance(seconds: float) -> None:
        clock[0] += seconds

    module.time = SimpleNamespace(monotonic=lambda: clock[0], sleep=advance)
    return module


def test_git_readiness_requires_nonempty_repository_and_actual_branch_response(monkeypatch) -> None:
    module = worker(monkeypatch)
    values = iter(
        [{"empty": True}, {"empty": False}, 500, {"empty": False}, {"commit": {"id": "expected"}}]
    )
    requests = []

    def remote(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "GET" and kwargs["timeout"] == 2
        requests.append(path)
        value = next(values)
        if isinstance(value, int):
            response = httpx.Response(value, request=httpx.Request("GET", "https://target.invalid"))
            response.raise_for_status()
        return value

    module.remote = remote
    repository, branch, observations = module._git_visibility()
    assert repository["empty"] is False and branch["commit"]["id"] == "expected"
    assert observations == [
        {"repository_empty": True},
        {"repository_empty": False},
        {"read_status": 500},
        {"repository_empty": False},
    ]
    assert len(requests) == 5


def test_git_readiness_timeout_cannot_report_success(monkeypatch) -> None:
    module = worker(monkeypatch)
    module.remote = lambda *args, **kwargs: {"empty": True}
    with pytest.raises(RuntimeError, match="within 20 seconds"):
        module._git_visibility()
