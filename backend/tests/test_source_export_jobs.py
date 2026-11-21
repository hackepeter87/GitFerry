from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from gitferry.operations import ExecutorBusy, OperationExecutor
from gitferry.package_archive import receive_package_archive
from gitferry.plan_runner import BackgroundPlanWorker, PlanRunner
from gitferry.source_export_jobs import SourceExportJobs
from gitferry.source_exports import SourceExportConflict
from gitferry.state_migrations import MIGRATIONS, SCHEMA_VERSION
from gitferry.state_store import StateStore
from gitferry.transfer_plans import PlanService
from test_source_exports import configure, mock_reads, requested


def jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SourceExportJobs:
    source = configure(tmp_path)
    mock_reads(monkeypatch)
    return SourceExportJobs(source.state, source.paths, source.references)


def test_explicit_queue_uses_immutable_capture_and_archive(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.exports.prepare(requested(), export_id="unrequested")
    assert service.run_next() is None
    assert service.get("unrequested")["status"] == "prepared"
    queued = service.create("requested", requested())
    assert queued["status"] == "queued"
    assert service.create("requested", requested()) == queued
    result = service.run_next()
    assert result and result["status"] == "ready" and result["capture_status"] == "captured"
    assert service.get("unrequested")["capture_status"] == "prepared"
    stream, job = service.open_download("requested")
    with stream:
        raw = stream.read()
    assert len(raw) == job["archive_size"]
    assert hashlib.sha256(raw).hexdigest() == job["archive_sha256"]
    archive = tmp_path / "received.zip"
    archive.write_bytes(raw)
    artifact = receive_package_archive(archive, tmp_path / "received")
    assert artifact.document["recipient"]["instance_key"] == "b"
    assert service.queue("requested")["status"] == "ready"
    assert service.run_next() is None


def test_changed_request_cannot_reuse_export_id(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.create("stable", requested())
    changed = {**requested(), "include_metadata": False}
    with pytest.raises(SourceExportConflict, match="changed selection"):
        service.create("stable", changed)
    assert service.get("stable")["request"]["include_metadata"] is True


def test_recipients_are_local_registrations_without_remote_approval(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    recipients = service.recipients()
    assert {item["instance_key"] for item in recipients} == {"a", "b"}
    assert all(
        set(item) == {"instance_key", "display_name", "installation_marker"} for item in recipients
    )


def test_version_nine_upgrade_preserves_capture_and_does_not_queue_it(tmp_path) -> None:
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        for version in range(1, 10):
            connection.executescript(MIGRATIONS[version])
            connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, "synthetic"))
        connection.execute("PRAGMA user_version=9")
        connection.execute(
            "INSERT INTO source_exports VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "existing",
                "a" * 64,
                json.dumps(requested()),
                "{}",
                "captured",
                "b" * 64,
                "b" * 64,
                None,
                "synthetic",
                "synthetic",
            ),
        )
        before = connection.execute("SELECT * FROM source_exports").fetchone()
    state = StateStore(database)
    with state.read_connection() as connection:
        assert tuple(connection.execute("SELECT * FROM source_exports").fetchone()) == before
        assert (
            connection.execute("SELECT count(*) FROM source_export_deliveries").fetchone()[0] == 0
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_existing_executor_excludes_export_job(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.create("exclusive", requested())
    with OperationExecutor(service.state, "another-writer"):
        with pytest.raises(ExecutorBusy):
            service.run_next()
    assert service.get("exclusive")["status"] == "queued"
    assert service.run_next()["status"] == "ready"


def test_failure_is_visible_and_needs_explicit_retry(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.create("failure", requested())
    with monkeypatch.context() as fault:
        fault.setattr(
            service.exports,
            "_capture",
            lambda *args: (_ for _ in ()).throw(
                RuntimeError("synthetic-private-detail-must-not-leak")
            ),
        )
        result = service.run_next()
    assert result["status"] == "failed"
    assert "synthetic-private" not in json.dumps(result)
    assert service.run_next() is None
    assert service.queue("failure")["status"] == "queued"
    assert service.run_next()["status"] == "ready"


def test_changed_archive_cannot_be_downloaded(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.create("tampered", requested())
    service.run_next()
    archive = service.paths.work_directory / ".downloads/tampered/package.zip"
    raw = bytearray(archive.read_bytes())
    raw[-1] ^= 1
    archive.write_bytes(raw)
    with pytest.raises(SourceExportConflict, match="bytes changed"):
        service.open_download("tampered")


def test_unproven_published_archive_is_not_recovered(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.create("foreign", requested())
    directory = service.paths.work_directory / ".downloads/foreign"
    directory.mkdir(parents=True)
    (directory / "package.zip").write_bytes(b"unproven bytes")
    result = service.run_next()
    assert result["status"] == "failed" and result["archive_sha256"] is None
    assert (directory / "package.zip").read_bytes() == b"unproven bytes"


@pytest.mark.parametrize("crash", [False, True])
def test_real_process_restart_recovers_only_confirmed_archive_evidence(
    tmp_path, monkeypatch, crash: bool
) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.create("restart", requested())
    # Capture once. The child must package this immutable capture without source
    # network access or test-harness-injected digests/target identifiers.
    service.exports.run("restart")
    program = """
import os, sys, json
from pathlib import Path
from gitferry.runtime import RuntimePaths
from gitferry.source_export_jobs import SourceExportJobs
from gitferry.state_store import StateStore
root=Path(sys.argv[1])
db=root/'state.sqlite3'
service=SourceExportJobs(StateStore(db),RuntimePaths(db,root/'packages',root/'work'))
def offline(*args,**kwargs): raise AssertionError('capture attempted during offline restart')
service.exports._capture=offline
hook=(lambda: os._exit(91)) if sys.argv[2]=='crash' else None
service.run_next(after_publish_hook=hook)
print(json.dumps(service.get('restart')))
"""
    args = [sys.executable, "-c", program, str(tmp_path)]
    first = subprocess.run(  # noqa: S603
        [*args, "crash" if crash else "normal"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert first.returncode == (91 if crash else 0), first.stderr
    if crash:
        assert service.get("restart")["status"] == "running"
    second = subprocess.run(  # noqa: S603
        [*args, "normal"], capture_output=True, text=True, check=True, timeout=20
    )
    result = json.loads(second.stdout)
    assert result["status"] == "ready"
    with service.open_download("restart")[0] as stream:
        assert hashlib.sha256(stream.read()).hexdigest() == result["archive_sha256"]


def test_background_worker_continues_without_browser(tmp_path, monkeypatch) -> None:
    service = jobs(tmp_path, monkeypatch)
    service.create("background", requested())
    completed = threading.Event()

    def run_export():
        result = service.run_next()
        if result:
            completed.set()
        return result

    runner = PlanRunner(
        PlanService(service.state), lambda _: (_ for _ in ()).throw(AssertionError())
    )
    worker = BackgroundPlanWorker(runner, run_export=run_export)
    worker.start()
    try:
        assert completed.wait(10), "background command did not reach a concrete result"
        assert service.get("background")["status"] == "ready"
    finally:
        worker.stop()
    assert not worker.thread.is_alive()
