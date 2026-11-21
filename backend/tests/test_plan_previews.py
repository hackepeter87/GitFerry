from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import pytest
from gitferry.operations import ExecutorBusy, OperationExecutor
from gitferry.plan_diagnostics import PreflightBlocked, PreflightCheck
from gitferry.plan_runner import BackgroundPlanWorker, PlanRunner
from gitferry.state_migrations import SCHEMA_VERSION
from gitferry.state_store import StateStore
from gitferry.transfer_plans import PlanConflict, PlanService
from test_plan_boundaries import _client
from test_transfer_plans import _plan, _store


class ReadOnlyAdapter:
    def preflight(self, plan):
        pass

    def apply(self, plan, executor):
        pytest.fail("preview must never invoke target writes")


def setup(tmp_path):
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = _plan(service, evidence)
    runner = PlanRunner(service, lambda _: nullcontext(ReadOnlyAdapter()))
    return store, service, plan, runner


def test_preview_is_separate_from_execution_and_queue_requires_exact_generation(tmp_path):
    store, service, plan, runner = setup(tmp_path)
    identifier, digest = plan["plan_id"], plan["digest"]
    with pytest.raises(PlanConflict):
        service.queue(identifier, digest, preview_generation=1)
    requested = service.request_preview(identifier, digest)
    assert requested["preview"]["status"] == "queued" and requested["status"] == "draft"
    assert service.request_preview(identifier, digest)["preview"]["generation"] == 1
    checked = runner.preview_next()
    assert checked["preview"]["status"] == "passed" and checked["status"] == "draft"
    assert store.get_operation("op-1").status == "prepared"
    service.request_preview(identifier, digest)
    with pytest.raises(PlanConflict):
        service.queue(identifier, digest, preview_generation=1)
    assert runner.preview_next()["preview"]["generation"] == 2
    assert service.queue(identifier, digest, preview_generation=2)["status"] == "queued"
    with store.read_connection() as c:
        assert c.execute("SELECT count(*) FROM plan_previews").fetchone()[0] == 2


def test_target_collision_is_scoped_and_does_not_authorize_writes(tmp_path):
    store, service, plan, _ = setup(tmp_path)
    service.request_preview(plan["plan_id"], plan["digest"])

    class Collision(ReadOnlyAdapter):
        def preflight(self, plan):
            raise PreflightBlocked(PreflightCheck.STRUCTURE, "owner/repo")

    result = PlanRunner(service, lambda _: nullcontext(Collision())).preview_next()
    assert result["status"] == "draft"
    assert result["preview"]["status"] == "blocked"
    assert result["preview"]["reason"] == PreflightCheck.STRUCTURE.value
    assert result["preview"]["scope"] == "owner/repo"
    assert store.get_operation("op-1").status == "prepared"
    with pytest.raises(PlanConflict):
        service.queue(plan["plan_id"], plan["digest"], preview_generation=1)


def test_authority_change_during_reads_and_raw_exception_never_become_success(tmp_path):
    store, service, plan, _ = setup(tmp_path)
    service.request_preview(plan["plan_id"], plan["digest"])

    class Revoked(ReadOnlyAdapter):
        def preflight(self, plan):
            with store.transaction() as c:
                c.execute("UPDATE connection_profiles SET locally_approved=0")

    result = PlanRunner(service, lambda _: nullcontext(Revoked())).preview_next()
    assert result["preview"]["status"] == "blocked"
    assert result["preview"]["reason"] == PreflightCheck.SELECTION.value
    with store.transaction() as c:
        c.execute("UPDATE connection_profiles SET locally_approved=1")
    service.request_preview(plan["plan_id"], plan["digest"])

    class Failed(ReadOnlyAdapter):
        def preflight(self, plan):
            raise RuntimeError("synthetic secret-like remote response must not persist")

    result = PlanRunner(service, lambda _: nullcontext(Failed())).preview_next()
    assert result["preview"]["status"] == "failed"
    assert "synthetic secret" not in json.dumps(result)


def test_unknown_journal_blocks_preview_without_remote_read_or_reset(tmp_path):
    store, service, plan, _ = setup(tmp_path)
    service.request_preview(plan["plan_id"], plan["digest"])
    with store.transaction() as c:
        c.execute("UPDATE operations SET status='outcome_unknown' WHERE operation_id='op-1'")

    def forbidden(_):
        pytest.fail("journal uncertainty must block before any remote check")

    result = PlanRunner(service, forbidden).preview_next()
    assert result["preview"]["status"] == "blocked"
    assert result["preview"]["reason"] == PreflightCheck.JOURNAL.value
    assert store.get_operation("op-1").status == "outcome_unknown"


def test_concurrent_requests_and_executors_cannot_duplicate_active_work(tmp_path):
    store, service, plan, _ = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: service.request_preview(plan["plan_id"], plan["digest"]), range(2))
        )
    assert [x["preview"]["generation"] for x in results] == [1, 1]
    with store.transaction() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO plan_previews SELECT plan_id,2,plan_digest,status,reason,scope,"
            "created_at,updated_at FROM plan_previews"
        )
    entered, release = threading.Event(), threading.Event()

    class Waiting(ReadOnlyAdapter):
        def preflight(self, plan):
            entered.set()
            assert release.wait(5)

    runner = PlanRunner(service, lambda _: nullcontext(Waiting()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runner.preview, plan["plan_id"])
        try:
            assert entered.wait(5)
            with pytest.raises(ExecutorBusy):
                runner.preview(plan["plan_id"])
            with pytest.raises(ExecutorBusy), OperationExecutor(store, "other-writer"):
                pytest.fail("write executor overlapped preview")
        finally:
            release.set()
        assert first.result(timeout=5)["preview"]["status"] == "passed"


def test_actual_process_crash_repeats_only_reads_and_retains_result(tmp_path: Path):
    store, service, plan, _ = setup(tmp_path)
    service.request_preview(plan["plan_id"], plan["digest"])
    script = """
import json, os, sys
from contextlib import nullcontext
from gitferry.plan_runner import PlanRunner
from gitferry.transfer_plans import PlanService
from gitferry.state_store import StateStore
class Adapter:
    def preflight(self, plan):
        if sys.argv[2] == 'crash': os._exit(92)
    def apply(self, plan, executor): raise RuntimeError('unexpected target write')
s = PlanService(StateStore(sys.argv[1]))
if sys.argv[2] == 'read': print(json.dumps(s.list()[0]))
else: print(json.dumps(PlanRunner(s, lambda _: nullcontext(Adapter())).preview_next()))
"""

    def child(mode):
        return subprocess.run(  # noqa: S603 -- fixed synthetic restart program
            [sys.executable, "-c", script, str(store.path), mode],
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert child("crash").returncode == 92
    assert service.get(plan["plan_id"])["preview"]["status"] == "checking"
    result = child("resume")
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["preview"]["status"] == "passed" and observed["status"] == "draft"
    assert observed["operations"][0]["status"] == "prepared"
    assert json.loads(child("read").stdout) == observed


def test_schema_11_upgrade_preserves_existing_plan_and_operation(tmp_path):
    store, service, plan, _ = setup(tmp_path)
    with store.transaction() as c:
        c.execute("DROP TABLE directory_configuration_events")
        c.execute("DROP TABLE directory_configurations")
        c.execute("DROP TABLE directory_bootstrap_events")
        c.execute("DROP TABLE directory_bootstraps")
        c.execute("DROP TABLE identity_decision_events")
        c.execute("DROP TABLE identity_candidates")
        c.execute("DROP TABLE plan_previews")
        c.execute("DELETE FROM schema_migrations WHERE version>=12")
        c.execute("PRAGMA user_version=11")
    migrated = StateStore(store.path)
    assert PlanService(migrated).get(plan["plan_id"]) == plan
    assert migrated.get_operation("op-1").status == "prepared"
    with migrated.read_connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_preview_api_persists_commands_and_never_accepts_success_overrides(tmp_path):
    store, service, plan, _ = setup(tmp_path)
    client, csrf = _client(store)
    entered, release = threading.Event(), threading.Event()

    class Adapter(ReadOnlyAdapter):
        def preflight(self, plan):
            entered.set()
            assert release.wait(5)

    worker = BackgroundPlanWorker(PlanRunner(service, lambda _: nullcontext(Adapter())))
    client.app.state.plan_worker = worker
    path = f"/api/v1/plans/{plan['plan_id']}"
    headers = {"x-gitferry-csrf": csrf}
    with client:
        worker.start()
        try:
            assert client.get(path).json()["preview"] is None
            assert (
                client.post(path + "/preview", json={"digest": plan["digest"]}).status_code == 403
            )
            assert (
                client.post(
                    path + "/preview",
                    json={"digest": plan["digest"], "status": "passed"},
                    headers=headers,
                ).status_code
                == 409
            )
            response = client.post(
                path + "/preview", json={"digest": plan["digest"]}, headers=headers
            )
            assert response.status_code == 200 and response.json()["status"] == "draft"
            assert entered.wait(5)
            pending = client.get(path).json()
            assert pending["preview"]["status"] == "checking" and pending["status"] == "draft"
            assert (
                client.post(
                    path + "/queue",
                    json={"digest": plan["digest"], "preview_generation": 1},
                    headers=headers,
                ).status_code
                == 409
            )
            assert (
                client.post(
                    path + "/queue",
                    json={"digest": plan["digest"], "preview_generation": True},
                    headers=headers,
                ).status_code
                == 409
            )
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if client.get(path).json()["preview"]["status"] == "passed":
                break
            time.sleep(0.02)
        final = client.get(path).json()
        assert final["preview"]["status"] == "passed" and final["status"] == "draft"
        stale = client.post(
            path + "/queue",
            json={"digest": plan["digest"], "preview_generation": 2},
            headers=headers,
        )
        assert stale.status_code == 409
        assert stale.json()["error"] == {
            "code": "plan_conflict",
            "message": (
                "Transfer plan cannot be queued. Refresh and review the plan and prerequisites."
            ),
        }
        assert client.get(path).json()["status"] == "draft"
        assert store.get_operation("op-1").status == "prepared"
    assert not worker.thread.is_alive()
