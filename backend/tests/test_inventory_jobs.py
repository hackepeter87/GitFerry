from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from gitferry.connection_probe import approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileConflict, ProfileService
from gitferry.content_inventory import (
    CONTENT_CLASSES,
    TRANSFER_CLASSES,
    InventoryError,
    parse_inventory,
)
from gitferry.inventory_jobs import InventoryJobs, parse_request
from gitferry.operations import ExecutorBusy
from gitferry.state_store import StateStore


def _response(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/version"):
        return httpx.Response(200, json={"version": "1.27.2"})
    if path.endswith("/user"):
        return httpx.Response(200, json={"id": 42, "login": "synthetic-admin"})
    raise AssertionError(path)


def _configured(tmp_path: Path) -> tuple[ProfileService, InventoryJobs]:
    secrets = tmp_path / "secrets"
    secrets.mkdir(parents=True)
    token = secrets / "synthetic-token"
    token.write_text("synthetic-test-token\n")
    token.chmod(0o600)
    state = StateStore(tmp_path / "state.sqlite3")
    profiles = ProfileService(state)
    profiles.create(
        ConnectionProfile(
            "a", "Synthetic", "https://forge.invalid/subpath", "install-a", "synthetic-token"
        )
    )
    from gitferry.connection_probe import LocalReferences

    references = LocalReferences(secrets, tmp_path / "cas")
    evidence = probe_connection(
        profiles,
        "a",
        expected_revision=1,
        references=references,
        transport=httpx.MockTransport(_response),
    )
    approve_connection(
        profiles, "a", expected_revision=1, evidence_digest=evidence["evidence_digest"]
    )
    return profiles, InventoryJobs(state, references)


def _request() -> dict[str, Any]:
    return {
        "source_instance_key": "a",
        "source_profile_revision": 1,
        "repositories": [{"repository": "synthetic/selected", "global_id": "7"}],
        "requested_classes": sorted(TRANSFER_CLASSES),
    }


def _inventory_object(name: str) -> dict[str, object]:
    native_ids = {
        "labels": "11",
        "releases": "21",
        "attachments": "31",
        "release_assets": "32",
    }
    native_id = native_ids.get(name)
    facts: dict[str, object] = {"value": name}
    author = (
        {"account_id": "1", "login": "synthetic-author"}
        if name in {"issues", "comments", "releases"}
        else None
    )
    parent = None
    size_bytes = None
    if name in {"teams", "permissions"}:
        facts.update(
            permission="read",
            units_map={"repo.code": "read", "repo.issues": "read"},
        )
    elif name == "labels":
        facts = {"name": "priority", "color": "112233", "description": "Synthetic"}
    elif name == "releases":
        facts = {
            "tag_name": "v1",
            "target_commitish": "main",
            "name": "Release",
            "body": "Notes",
            "draft": False,
            "prerelease": False,
            "created_at": "2026-01-01T00:00:00Z",
            "published_at": "2026-01-01T00:00:00Z",
        }
    elif name in {"attachments", "release_assets"}:
        parent = {
            "content_class": "comments" if name == "attachments" else "releases",
            "key": "comments-1" if name == "attachments" else "21",
        }
        facts = {
            "uuid": "synthetic-uuid",
            "name": "asset.txt",
            "created_at": "2026-01-01T00:00:00Z",
        }
        size_bytes = 5
    return {
        "key": native_id or f"{name}-1",
        "namespace": "repo",
        "facts": facts,
        "author": author,
        "native_id": native_id,
        "parent": parent,
        "size_bytes": size_bytes,
    }


def _document(job: dict[str, Any], *, repository: str = "synthetic/selected", source: str = "a"):
    requested = job["request"]["requested_classes"]
    return parse_inventory(
        {
            "format": "gitferry.content-inventory",
            "schema_version": 1,
            "comparison_version": 1,
            "snapshot_id": job["inventory_id"],
            "created_at": job["created_at"],
            "source_instance_key": source,
            "source_installation_marker": "install-a",
            "gitea_version": "1.27.2",
            "repositories": [
                {
                    "repository": repository,
                    "native_id": "7",
                    "requested_classes": requested,
                    "classes": [
                        {
                            "content_class": name,
                            "status": "observed",
                            "count": 1,
                            "objects": [_inventory_object(name)],
                            "reason": "",
                            "limitations": [],
                        }
                        for name in CONTENT_CLASSES
                    ],
                }
            ],
            "deletion_semantics": "absence-does-not-delete",
        }
    )


def _ready(tmp_path: Path):
    profiles, jobs = _configured(tmp_path)
    jobs.create("inventory-1", _request())
    result = jobs.run_next(observe=lambda job: _document(job))
    assert result is not None and result["status"] == "ready"
    return profiles, jobs, result


def test_create_is_idempotent_but_changed_intent_is_denied(tmp_path: Path) -> None:
    _, jobs = _configured(tmp_path)
    request = _request()
    first = jobs.create("inventory-1", request)
    assert jobs.create("inventory-1", request)["request"] == first["request"]
    changed = copy.deepcopy(request)
    changed["repositories"][0]["global_id"] = "8"
    with pytest.raises(InventoryError, match="reused"):
        jobs.create("inventory-1", changed)


def test_confirmed_document_survives_get_and_process_restart(tmp_path: Path) -> None:
    _, jobs, result = _ready(tmp_path)
    digest = result["document_digest"]
    program = """
import json, sys
from gitferry.inventory_jobs import InventoryJobs
from gitferry.state_store import StateStore
jobs = InventoryJobs(StateStore(sys.argv[1]))
value = jobs.require_ready(sys.argv[2], sys.argv[3])
print(json.dumps({
    "digest": jobs.get(sys.argv[2])["document_digest"],
    "snapshot": value.snapshot_id,
}))
"""
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic database path
        [sys.executable, "-c", program, str(tmp_path / "state.sqlite3"), "inventory-1", digest],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {"digest": digest, "snapshot": "inventory-1"}


def test_blocked_result_keeps_safe_diagnostics_and_no_remote_error(tmp_path: Path) -> None:
    _, jobs = _configured(tmp_path)
    jobs.create("inventory-1", _request())

    def blocked(job: dict[str, Any]):
        value = _document(job)
        issue = next(c for c in value.repositories[0].classes if c.content_class == "issues")
        issue.status = "blocked"
        issue.count = None
        issue.objects = []
        issue.reason = "synthetic collector limitation"
        return value

    result = jobs.run_next(observe=blocked)
    assert result is not None and result["status"] == "blocked"
    assert result["error_code"] is None
    assert result["summary"]["blockers"] == [
        {
            "repository": "synthetic/selected",
            "content_class": "issues",
            "reason": "synthetic collector limitation",
        }
    ]


def test_running_job_is_retried_after_restart_without_remote_write_callback(tmp_path: Path) -> None:
    _, jobs = _configured(tmp_path)
    jobs.create("inventory-1", _request())
    queued = jobs.get("inventory-1")
    document_path = tmp_path / "document.json"
    document_path.write_text(json.dumps(_document(queued).model_dump(mode="json")))
    with jobs.state.transaction() as connection:
        connection.execute(
            "UPDATE source_inventory_jobs SET status='running' WHERE inventory_id=?",
            ("inventory-1",),
        )
    program = """
import json, sys
from gitferry.content_inventory import parse_inventory
from gitferry.inventory_jobs import InventoryJobs
from gitferry.state_store import StateStore
document = parse_inventory(json.loads(open(sys.argv[2]).read()))
jobs = InventoryJobs(StateStore(sys.argv[1]))
result = jobs.run_next(observe=lambda job: document)
print(json.dumps({"status": result["status"], "digest": result["document_digest"]}))
"""
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic database path
        [sys.executable, "-c", program, str(tmp_path / "state.sqlite3"), str(document_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(completed.stdout)
    assert output["status"] == "ready"
    assert output["digest"] == jobs.get("inventory-1")["document_digest"]


@pytest.mark.parametrize("change", ["update", "rebuild"])
def test_approved_profile_change_or_rebuild_blocks_require_ready(
    tmp_path: Path, change: str
) -> None:
    profiles, jobs, result = _ready(tmp_path)
    if change == "update":
        profiles.update(
            "a",
            expected_revision=1,
            display_name="Changed",
            credential_ref="synthetic-token",
            ca_ref=None,
        )
    else:
        profiles.replace_installation("a", expected_revision=1, installation_marker="install-b")
    with pytest.raises((InventoryError, ProfileConflict)):
        jobs.require_ready("inventory-1", result["document_digest"])


def test_changed_selected_fact_blocks_reobserve_without_overwriting_baseline(
    tmp_path: Path,
) -> None:
    _, jobs, result = _ready(tmp_path)
    changed = parse_inventory(result["document"])
    issue = next(c for c in changed.repositories[0].classes if c.content_class == "issues")
    issue.objects[0].facts["value"] = "changed"
    jobs._observe = lambda job: changed
    with pytest.raises(InventoryError, match="facts changed"):
        jobs.require_ready("inventory-1", result["document_digest"], reobserve=True)
    assert jobs.get("inventory-1")["document_digest"] == result["document_digest"]


def test_unrequested_fact_does_not_block_reobserve(tmp_path: Path) -> None:
    _, jobs, result = _ready(tmp_path)
    changed = parse_inventory(result["document"])
    wiki = next(c for c in changed.repositories[0].classes if c.content_class == "wiki")
    wiki.objects[0].facts["value"] = "unrequested-change"
    jobs._observe = lambda job: changed
    assert (
        jobs.require_ready("inventory-1", result["document_digest"], reobserve=True).snapshot_id
        == "inventory-1"
    )


def test_collector_identity_scope_and_digest_mismatches_fail_closed(tmp_path: Path) -> None:
    for variant in ("identity", "scope"):
        _, jobs = _configured(tmp_path / variant)
        jobs.create("inventory-1", _request())
        kwargs = {"source": "other"} if variant == "identity" else {"repository": "synthetic/other"}
        result = jobs.run_next(observe=lambda job, kwargs=kwargs: _document(job, **kwargs))
        assert result is not None and result["status"] == "failed"
        assert result["error_code"] == "inventory_failed"

    _, jobs = _configured(tmp_path / "digest")
    jobs.create("inventory-1", _request())
    result = jobs.run_next(observe=lambda job: _document(job))
    assert result is not None and result["status"] == "ready"
    with jobs.state.transaction() as connection:
        connection.execute(
            "UPDATE source_inventory_jobs SET document_digest=? WHERE inventory_id=?",
            ("0" * 64, "inventory-1"),
        )
    with pytest.raises(InventoryError, match="digest"):
        jobs.get("inventory-1")


def test_collector_exception_does_not_reflect_secret_or_path(tmp_path: Path) -> None:
    _, jobs = _configured(tmp_path)
    jobs.create("inventory-1", _request())
    sensitive_value = "synthetic-secret-token"
    path = str(tmp_path / "private-path")

    def fail(_: dict[str, Any]):
        raise RuntimeError(f"{sensitive_value} {path}")

    result = jobs.run_next(observe=fail)
    assert result is not None and result["status"] == "failed"
    assert result["error_code"] == "inventory_failed"
    assert sensitive_value not in repr(result) and path not in repr(result)


def test_concurrent_calls_have_one_executor_and_one_observation(tmp_path: Path) -> None:
    _, jobs = _configured(tmp_path)
    jobs.create("inventory-1", _request())
    calls: list[str] = []
    lock = threading.Lock()

    def observe(job: dict[str, Any]):
        with lock:
            calls.append(job["inventory_id"])
        return _document(job)

    with ThreadPoolExecutor(max_workers=2) as pool:

        def invoke(_: int):
            try:
                return jobs.run_next(observe=observe)
            except ExecutorBusy as error:
                return error

        results = list(pool.map(invoke, (1, 2)))
    assert calls == ["inventory-1"]
    assert (
        sum(
            not isinstance(result, ExecutorBusy)
            and result is not None
            and result["status"] == "ready"
            for result in results
        )
        == 1
    )
    assert sum(isinstance(result, ExecutorBusy) for result in results) == 1


@pytest.mark.parametrize(
    "value",
    [
        {
            "source_instance_key": "../escape",
            "source_profile_revision": 1,
            "repositories": [{"repository": "synthetic/selected", "global_id": "7"}],
            "requested_classes": ["issues"],
        },
        {
            "source_instance_key": "a",
            "source_profile_revision": 1,
            "repositories": [{"repository": "synthetic/selected", "global_id": True}],
            "requested_classes": ["issues"],
        },
        {
            "source_instance_key": "a",
            "source_profile_revision": 1,
            "repositories": [{"repository": "synthetic/selected", "global_id": "7"}],
            "requested_classes": ["not-a-class"],
        },
    ],
)
def test_invalid_selection_scope_is_rejected(value: dict[str, Any]) -> None:
    with pytest.raises(InventoryError):
        parse_request(value)
