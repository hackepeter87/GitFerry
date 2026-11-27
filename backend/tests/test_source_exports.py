from __future__ import annotations

import json
import os
import sqlite3
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any

import gitferry.source_exports as exports
import httpx
import pytest
from gitferry.application_package import PackageError, inspect_package
from gitferry.connection_probe import approve_connection, probe_connection
from gitferry.connection_profiles import ProfileConflict
from gitferry.runtime import RuntimePaths
from gitferry.source_export_state import record_source_capture
from gitferry.source_exports import SourceExportConflict, SourceExports
from gitferry.source_package import build_return_package, build_source_documents
from gitferry.source_snapshot import capture_source
from gitferry.state_store import OriginKey
from test_connection_probe import setup

STAMP = "2026-11-21T12:00:00Z"


def response(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    assert request.url.host == "forge.invalid"
    path = request.url.path.removeprefix("/subpath/api/v1")
    records: dict[str, Any] = {
        "/version": {"version": "1.27.2"},
        "/user": {"id": 1, "login": "technical-admin"},
        "/repos/synthetic/selected": {
            "id": 7,
            "full_name": "synthetic/selected",
            "owner": {"id": 3, "login": "synthetic"},
        },
        "/orgs/synthetic": {"id": 3, "name": "synthetic"},
        "/repos/synthetic/selected/teams": [
            {
                "id": 4,
                "name": "readers",
                "permission": "read",
                "units_map": {"repo.code": "read", "repo.issues": "read"},
            }
        ],
        "/teams/4/members": [{"id": 11, "login": "issue-author"}],
        "/teams/4": {
            "id": 4,
            "name": "readers",
            "permission": "read",
            "units_map": {"repo.code": "read", "repo.issues": "read"},
        },
        "/repos/synthetic/selected/issues": [
            {
                "id": 101,
                "number": 1,
                "title": "Captured issue",
                "body": "Markdown **body**\nFähre 🚢",
                "state": "open",
                "labels": [],
                "user": {"id": 11, "login": "issue-author"},
                "created_at": STAMP,
                "updated_at": STAMP,
            }
        ],
        "/repos/synthetic/selected/issues/1/comments": [
            {
                "id": 201,
                "body": "Comment\n**Unicode ä**",
                "user": {"id": 12, "login": "comment-author"},
                "created_at": STAMP,
                "updated_at": STAMP,
            }
        ],
    }
    assert path in records, path
    return httpx.Response(200, json=records[path])


def requested() -> dict[str, Any]:
    return {
        "version": 1,
        "source_instance_key": "a",
        "source_profile_revision": 1,
        "recipient_instance_key": "b",
        "repositories": [{"repository": "synthetic/selected", "global_id": "7"}],
        "include_metadata": True,
        "include_members": True,
    }


def test_build_return_package_preserves_forward_facts_and_reverses_endpoints(
    tmp_path: Path,
) -> None:
    service = configure(tmp_path)
    package = build_return_package(
        service.state,
        package_id="return-1",
        transfer_round_id="round-1",
        created_at=STAMP,
        sender="b",
        recipient="a",
        forward_package_id="forward-1",
        forward_package_digest="a" * 64,
        repositories=[
            {
                "origin": {"instance_key": "a", "global_id": "7"},
                "repository": "synthetic/selected",
                "comparison": {
                    "original_baseline": {"refs/heads/main": "1" * 40},
                    "confirmed_import": {"refs/heads/main": "1" * 40},
                    "desired": {"refs/heads/main": "2" * 40},
                },
            }
        ],
    )
    assert package["mode"] == "return"
    assert package["sender"]["instance_key"] == "b"
    assert package["recipient"]["instance_key"] == "a"
    assert (
        package["repositories"][0]["comparison"]["original_baseline"]["refs/heads/main"]
        == "1" * 40
    )


def test_return_export_request_requires_forward_digest() -> None:
    value = requested()
    value.update(
        version=5,
        mode="return",
        forward_package_id="forward-1",
        include_metadata=False,
        include_members=False,
    )
    with pytest.raises(SourceExportConflict, match="invalid source export selection"):
        exports.parse_export_request(value)


def configure(tmp_path: Path) -> SourceExports:
    profiles, refs = setup(tmp_path)
    evidence = probe_connection(
        profiles, "a", expected_revision=1, references=refs, transport=httpx.MockTransport(response)
    )
    approve_connection(
        profiles, "a", expected_revision=1, evidence_digest=evidence["evidence_digest"]
    )
    profiles.store.register_instance(
        "b", "Offline recipient", "https://recipient.invalid", "install-b"
    )
    for name in ("packages", "work"):
        (tmp_path / name).mkdir()
    return SourceExports(
        profiles.store,
        RuntimePaths(profiles.store.path, tmp_path / "packages", tmp_path / "work"),
        refs,
    )


class EmptyGit:
    def __init__(self, *args: Any, **kwargs: Any):
        pass

    def __enter__(self) -> EmptyGit:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def export_bundle(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _git_remote(self, *args: Any) -> dict[str, str]:
        return {}

    def _repository_url(self, repository: str) -> str:
        return repository


def mock_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return response(request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        exports,
        "probe_connection",
        lambda *args, **kwargs: probe_connection(*args, **kwargs, transport=transport),
    )
    monkeypatch.setattr(
        exports,
        "capture_source",
        lambda *args, **kwargs: capture_source(*args, **kwargs, transport=transport),
    )
    monkeypatch.setattr(exports, "GiteaBundlePusher", EmptyGit)
    return calls


def test_capture_records_origins_baselines_and_claims_without_approving_authors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = configure(tmp_path)
    calls = mock_reads(monkeypatch)
    job = service.prepare(requested())
    staging = tmp_path / "work" / job["export_id"]
    staging.mkdir()
    (staging / "partial").write_text("synthetic unfinished capture")
    result = service.run(job["export_id"])
    assert result["status"] == "captured"
    package = inspect_package(tmp_path / "packages" / job["export_id"])
    manifest = json.loads((package.directory / "metadata/manifest.json").read_text())
    assert manifest["objects"][1]["author"]["login"] == "comment-author"
    assert package.document["metadata"]["issue_authors"][0]["author"]["login"] == "issue-author"
    assert all(claim["ou_path"] == "/" for claim in manifest["identity_claims"])
    assert all(
        claim["relationship"]["status"] == "observed-not-approved"
        for claim in manifest["identity_claims"]
    )
    assert all("recipient.invalid" not in value for value in calls)
    with service.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM object_origins").fetchone()[0] == 5
        assert connection.execute("SELECT count(*) FROM object_mappings").fetchone()[0] == 5
        assert connection.execute("SELECT count(*) FROM baselines").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
    before = len(calls)
    assert service.run(job["export_id"])["package_digest"] == result["package_digest"]
    assert len(calls) == before
    assert not list((tmp_path / "work").iterdir())


def test_selected_drift_leaves_no_package_or_partial_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = configure(tmp_path)
    mock_reads(monkeypatch)
    capture = exports.capture_source
    count = 0

    def drifting(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal count
        result = capture(*args, **kwargs)
        count += 1
        if count == 2:
            result["repositories"][0]["issues"][0]["body"] = "Changed during capture"
        return result

    monkeypatch.setattr(exports, "capture_source", drifting)
    job = service.prepare(requested())
    with pytest.raises(SourceExportConflict, match="changed while capturing"):
        service.run(job["export_id"])
    assert service.get(job["export_id"])["status"] == "failed"
    assert not (tmp_path / "packages" / job["export_id"]).exists()
    with service.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM object_origins").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM baselines").fetchone()[0] == 0


def test_changed_source_profile_blocks_before_any_remote_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = configure(tmp_path)
    calls = mock_reads(monkeypatch)
    job = service.prepare(requested())
    service.profiles.update(
        "a", expected_revision=1, display_name="Changed", credential_ref="rotated", ca_ref=None
    )
    staging = tmp_path / "work" / job["export_id"]
    staging.mkdir()
    (staging / "partial").write_text("synthetic unfinished capture")
    with pytest.raises(ProfileConflict):
        service.run(job["export_id"])
    assert calls == []
    assert service.get(job["export_id"])["status"] == "failed"
    assert not list((tmp_path / "packages").iterdir())
    assert not list((tmp_path / "work").iterdir())


def test_structure_only_export_has_no_metadata_or_author_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = configure(tmp_path)
    calls = mock_reads(monkeypatch)
    request = requested()
    request.update(include_metadata=False, include_members=False)
    job = service.prepare(request)
    service.run(job["export_id"])
    artifact = inspect_package(tmp_path / "packages" / job["export_id"])
    assert artifact.document["metadata"] is None
    assert artifact.document["realms"] == []
    assert all("/issues" not in call and "/members" not in call for call in calls)
    with service.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM baselines").fetchone()[0] == 0


def test_export_id_and_candidate_bytes_are_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = configure(tmp_path)
    mock_reads(monkeypatch)
    job = service.prepare(requested(), export_id="fixed-export")
    changed = requested()
    changed["include_members"] = False
    with pytest.raises(SourceExportConflict, match="changed selection"):
        service.prepare(changed, export_id=job["export_id"])
    service.run(job["export_id"])
    path = tmp_path / "packages" / job["export_id"] / "package.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(PackageError):
        service.run(job["export_id"])


def test_source_transaction_rolls_back_all_facts_when_completion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = configure(tmp_path)
    mock_reads(monkeypatch)
    job = service.prepare(requested())
    with service.state.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_capture BEFORE UPDATE OF status ON source_exports "
            "WHEN NEW.status='captured' BEGIN "
            "SELECT RAISE(ABORT,'synthetic capture fault'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.run(job["export_id"])
    with service.state.read_connection() as connection:
        for table in (
            "object_origins",
            "object_mappings",
            "baselines",
            "transfer_rounds",
            "transport_manifests",
        ):
            assert (
                connection.execute(
                    f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed table names above
                ).fetchone()[0]
                == 0
            )
    artifact = inspect_package(tmp_path / "packages" / job["export_id"])
    with service.state.transaction() as connection:
        connection.execute("DROP TRIGGER fail_capture")
        connection.execute(
            "UPDATE source_exports SET candidate_digest=? WHERE export_id=?",
            ("0" * 64, job["export_id"]),
        )
    with pytest.raises(PackageError, match="locally captured byte digest"):
        record_source_capture(service.state, artifact, job["export_id"])
    with service.state.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM object_origins").fetchone()[0] == 0
        connection.execute(
            "UPDATE source_exports SET candidate_digest=? WHERE export_id=?",
            (artifact.digest_sha256, job["export_id"]),
        )
    record_source_capture(service.state, artifact, job["export_id"])
    assert service.get(job["export_id"])["status"] == "captured"


def test_existing_copy_keeps_original_origin_and_parent(tmp_path: Path) -> None:
    service = configure(tmp_path)
    state = service.state
    state.register_instance("origin", "Original", "https://origin.invalid", "origin-install")
    issue = state.register_origin(
        OriginKey(
            "origin",
            "synthetic/selected",
            "issue",
            "9001",
            issue_index=9,
            origin_created_at="2025-01-01T00:00:00Z",
        )
    )
    comment = state.register_origin(
        OriginKey(
            "origin",
            "synthetic/selected",
            "comment",
            "9002",
            parent_origin_id=issue.id,
            origin_created_at="2025-01-02T00:00:00Z",
        )
    )
    state.add_object_mapping(
        issue.id,
        "a",
        "synthetic/selected",
        "101",
        target_issue_index=1,
        target_native_created_at=STAMP,
    )
    state.add_object_mapping(
        comment.id,
        "a",
        "synthetic/selected",
        "201",
        target_issue_index=1,
        target_native_created_at=STAMP,
    )
    snapshot = capture_source(
        "https://forge.invalid/subpath",
        "synthetic-test-token",
        ssl.create_default_context(),
        requested()["repositories"],
        include_metadata=True,
        include_members=True,
        transport=httpx.MockTransport(response),
    )
    _, manifest = build_source_documents(
        state,
        snapshot,
        export_id="capture-1",
        created_at=STAMP,
        source="a",
        recipient="b",
        include_metadata=True,
    )
    assert manifest is not None
    original, child = manifest["objects"]
    assert original["origin"]["global_id"] == "9001"
    assert original["origin"]["instance_key"] == "origin"
    assert original["replicas"][0]["global_id"] == "101"
    assert original["origin"]["created_at"] == "2025-01-01T00:00:00Z"
    assert child["origin"]["global_id"] == "9002"
    assert child["origin"]["parent_issue_origin"]["global_id"] == "9001"


def test_published_capture_recovers_in_a_real_new_process_without_source_access(
    tmp_path: Path,
) -> None:
    service = configure(tmp_path)
    job = service.prepare(requested(), export_id="restart-export")
    environment = dict(os.environ, PYTHONPATH="backend/src:backend/tests")
    program = """
import os, sys
from pathlib import Path
from pytest import MonkeyPatch
from test_source_exports import mock_reads
from gitferry.source_exports import SourceExports
from gitferry.state_store import StateStore
from gitferry.runtime import RuntimePaths
from gitferry.connection_probe import LocalReferences
root=Path(sys.argv[1])
state=StateStore(root/'state.sqlite3')
paths=RuntimePaths(state.path,root/'packages',root/'work')
refs=LocalReferences((root/'secrets').resolve(),root.resolve())
service=SourceExports(state,paths,refs)
mock_reads(MonkeyPatch())
service.run('restart-export',after_publish_hook=lambda:os._exit(93))
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic test program
        [sys.executable, "-c", program, str(tmp_path)],
        env=environment,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 93, result.stderr.decode()
    assert service.get(job["export_id"])["status"] == "capturing"
    with service.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM baselines").fetchone()[0] == 0
    recovery = (
        program[: program.index("mock_reads(MonkeyPatch())")]
        + "print(service.run('restart-export')['status'])\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic test program
        [sys.executable, "-c", recovery, str(tmp_path)],
        env=environment,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().strip() == "captured"
    assert service.get(job["export_id"])["status"] == "captured"
    assert not list((tmp_path / "work").iterdir())


def test_known_structure_copies_keep_permanent_origins_and_source_replicas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = configure(tmp_path)
    mock_reads(monkeypatch)
    state = service.state
    state.register_instance("origin", "Original", "https://origin.invalid", "origin-install")
    structures = (
        ("organization", "synthetic", "903", "3"),
        ("repository", "synthetic/selected", "907", "7"),
        ("team", "synthetic", "904", "4"),
    )
    identifiers = []
    for kind, scope, original_id, local_id in structures:
        origin = state.register_origin(OriginKey("origin", scope, kind, original_id))
        state.add_object_mapping(origin.id, "a", scope, local_id)
        identifiers.append(origin.id)
    job = service.prepare(requested())
    service.run(job["export_id"])
    with state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM object_origins").fetchone()[0] == 5
        for identifier, (_, scope, _, local_id) in zip(identifiers, structures, strict=False):
            row = connection.execute(
                "SELECT target_repository_scope,target_global_id FROM object_mappings "
                "WHERE origin_id=? AND target_instance_key='a'",
                (identifier,),
            ).fetchone()
            assert tuple(row) == (scope, local_id)


@pytest.mark.parametrize(
    "kind,scope,native_id",
    [
        ("organization", "synthetic", "3"),
        ("repository", "synthetic/selected", "7"),
        ("team", "synthetic", "4"),
    ],
)
def test_renamed_structure_copy_is_rejected_without_inventing_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    scope: str,
    native_id: str,
) -> None:
    from gitferry.state_store import MappingConflict

    service = configure(tmp_path)
    mock_reads(monkeypatch)
    state = service.state
    state.register_instance("origin", "Original", "https://origin.invalid", "origin-install")
    origin = state.register_origin(OriginKey("origin", "old-scope", kind, "900"))
    state.add_object_mapping(origin.id, "a", scope, native_id)
    job = service.prepare(requested())
    with pytest.raises(MappingConflict, match="cross-scope"):
        service.run(job["export_id"])
    assert service.get(job["export_id"])["status"] == "failed"
    with state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM object_origins").fetchone()[0] == 1
    assert not list((tmp_path / "packages").iterdir())
