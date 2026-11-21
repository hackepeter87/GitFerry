from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.cli import build_parser
from gitferry.plan_cli import execute
from gitferry.state_store import StateStore
from test_plan_application import configured_plan


def _client(state: StateStore) -> tuple[TestClient, str]:
    auth = AuthService(state)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    client = TestClient(create_app(auth_service=auth), base_url="https://testserver")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "synthetic.admin", "password": "correct synthetic passphrase"},
    )
    assert response.status_code == 200
    return client, client.cookies["gitferry_csrf"]


def test_plan_http_boundary_requires_admin_csrf_and_worker_for_queue(tmp_path: Path) -> None:
    state, request = configured_plan(tmp_path)
    client, csrf = _client(state)
    with client:
        assert client.get("/api/v1/plans").status_code == 200
        assert client.post("/api/v1/plans/prepare", json=request).status_code == 403
        headers = {"x-gitferry-csrf": csrf}
        prepared = client.post("/api/v1/plans/prepare", json=request, headers=headers)
        assert prepared.status_code == 200
        plan = prepared.json()
        queued = client.post(
            f"/api/v1/plans/{plan['plan_id']}/queue",
            json={"digest": plan["digest"]},
            headers=headers,
        )
        assert queued.status_code == 503
        assert "synthetic" not in queued.text


def test_plan_cli_uses_bounded_json_and_same_persisted_plan(tmp_path: Path, capsys) -> None:
    state, request = configured_plan(tmp_path)
    input_path = tmp_path / "selection.json"
    input_path.write_text(json.dumps(request), encoding="utf-8")
    args = build_parser().parse_args(
        ["plans", "prepare", "--input", str(input_path), "--database", str(state.path)]
    )
    assert execute(args) == 0
    plan = json.loads(capsys.readouterr().out)
    listed = build_parser().parse_args(["plans", "list", "--database", str(state.path)])
    assert execute(listed) == 0
    assert plan["plan_id"] in capsys.readouterr().out


def test_plan_cli_rejects_duplicate_json_keys(tmp_path: Path, capsys) -> None:
    state, _ = configured_plan(tmp_path)
    input_path = tmp_path / "duplicate.json"
    input_path.write_text('{"version":1,"version":1}', encoding="utf-8")
    args = build_parser().parse_args(
        ["plans", "prepare", "--input", str(input_path), "--database", str(state.path)]
    )
    assert execute(args) == 2
    assert "invalid or unavailable" in capsys.readouterr().out


def test_invalid_origin_is_a_bounded_client_error(tmp_path: Path) -> None:
    state, request = configured_plan(tmp_path)
    request["metadata"][0]["origin_id"] = "missing"
    client, csrf = _client(state)
    with client:
        response = client.post(
            "/api/v1/plans/prepare", json=request, headers={"x-gitferry-csrf": csrf}
        )
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_managed_worker_survives_http_client_disconnect(tmp_path: Path, monkeypatch) -> None:
    import threading
    import time

    from gitferry import api as api_module
    from gitferry.runtime import RuntimeContext, RuntimePaths
    from gitferry.state_store import TargetConfirmation
    from gitferry.transfer_plans import PlanService

    state, selection = configured_plan(tmp_path)
    selection["metadata"] = selection["metadata"][1:]
    auth = AuthService(state)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Adapter:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def preflight(self, plan):
            pass

        def apply(self, plan, executor):
            def write(_):
                calls.append("write")
                entered.set()
                assert release.wait(5), "test did not release the simulated remote write"
                return TargetConfirmation("101", 1)

            executor.execute(plan["operations"][0]["operation_id"], write)

    paths = RuntimePaths(state.path, tmp_path, tmp_path)
    monkeypatch.setenv("GITFERRY_RUNTIME_MANAGED", "1")
    monkeypatch.setattr(api_module, "initialize_runtime", lambda: RuntimeContext(paths, state))
    monkeypatch.setattr(api_module, "GiteaPlanAdapter", lambda *args: Adapter())
    app = create_app(auth_service=auth)
    with TestClient(app, base_url="https://testserver") as server:
        browser = TestClient(app, base_url="https://testserver")
        login = browser.post(
            "/api/v1/auth/login",
            json={"username": "synthetic.admin", "password": "correct synthetic passphrase"},
        )
        assert login.status_code == 200
        headers = {"x-gitferry-csrf": browser.cookies["gitferry_csrf"]}
        plan = browser.post("/api/v1/plans/prepare", json=selection, headers=headers).json()
        response = browser.post(
            f"/api/v1/plans/{plan['plan_id']}/queue",
            json={"digest": plan["digest"]},
            headers=headers,
        )
        assert response.status_code == 200
        assert entered.wait(5)
        browser.close()
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = PlanService(state).get(plan["plan_id"])
            if result["status"] == "confirmed":
                break
            time.sleep(0.02)
        assert result["status"] == "confirmed"
        assert calls == ["write"]
        assert server.get("/readyz").status_code == 200
    assert not app.state.plan_worker.thread.is_alive()
