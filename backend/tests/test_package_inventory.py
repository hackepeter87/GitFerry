from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from gitferry.application_package import PackageError, inspect_package, write_package
from gitferry.content_inventory import CONTENT_CLASSES, export_classes, parse_inventory
from gitferry.inventory_jobs import InventoryJobs
from gitferry.package_archive import receive_package_archive, write_package_archive
from gitferry.package_inventory import inventory_report
from gitferry.source_exports import SourceExportConflict, parse_export_request
from gitferry.source_snapshot import capture_source
from gitferry.state_codec import canonical_json
from test_source_exports import configure, mock_reads, requested, response


def inventoried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service = configure(tmp_path)
    mock_reads(monkeypatch)
    request = requested()
    inventory_request = {
        key: request[key]
        for key in ("source_instance_key", "source_profile_revision", "repositories")
    }
    inventory_request["requested_classes"] = export_classes(
        metadata=request["include_metadata"], members=request["include_members"]
    )
    jobs = InventoryJobs(service.state, service.references)
    job = jobs.create("inventory-review", inventory_request)
    import ssl

    snapshot = capture_source(
        "https://forge.invalid/subpath",
        "synthetic-inventory-private-credential",
        ssl.create_default_context(),
        request["repositories"],
        include_metadata=True,
        include_members=True,
        transport=httpx.MockTransport(response),
    )
    repo = snapshot["repositories"][0]
    classes: dict[str, list[dict[str, Any]]] = {
        "git_refs": [],
        "organizations": [
            {
                "key": "3",
                "native_id": "3",
                "namespace": "organization-id",
                "facts": {"name": "synthetic"},
            }
        ],
        "teams": [
            {
                "key": "4",
                "native_id": "4",
                "namespace": "team-id",
                "facts": {
                    "name": "readers",
                    "permission": "read",
                    "units_map": {"repo.code": "read", "repo.issues": "read"},
                    "members": repo["teams"][0]["members"],
                },
            }
        ],
        "permissions": [],
        "identities": [],
        "issues": [],
        "comments": [],
    }
    for issue in repo["issues"]:
        author = {"account_id": issue["author"]["global_id"], "login": issue["author"]["login"]}
        classes["issues"].append(
            {
                "key": issue["global_id"],
                "native_id": issue["global_id"],
                "namespace": "global-issue-id",
                "issue_index": str(issue["issue_index"]),
                "author": author,
                "facts": {
                    k: issue[k] for k in ("title", "body", "state", "created_at", "updated_at")
                },
            }
        )
        for comment in issue["comments"]:
            classes["comments"].append(
                {
                    "key": comment["global_id"],
                    "native_id": comment["global_id"],
                    "namespace": "global-comment-id",
                    "parent": {"content_class": "issues", "key": issue["global_id"]},
                    "author": {
                        "account_id": comment["author"]["global_id"],
                        "login": comment["author"]["login"],
                    },
                    "facts": {k: comment[k] for k in ("body", "created_at", "updated_at")},
                }
            )
    document = parse_inventory(
        {
            "format": "gitferry.content-inventory",
            "schema_version": 1,
            "comparison_version": 1,
            "snapshot_id": job["inventory_id"],
            "created_at": job["created_at"],
            "source_instance_key": "a",
            "source_installation_marker": "install-a",
            "gitea_version": "1.27.2",
            "deletion_semantics": "absence-does-not-delete",
            "repositories": [
                {
                    "repository": "synthetic/selected",
                    "native_id": "7",
                    "requested_classes": inventory_request["requested_classes"],
                    "classes": [
                        {
                            "content_class": key,
                            "status": "observed" if key in classes else "not-checked",
                            "count": len(classes[key]) if key in classes else None,
                            "objects": classes.get(key, []),
                            "reason": "" if key in classes else "Excluded test scope",
                        }
                        for key in CONTENT_CLASSES
                    ],
                }
            ],
        }
    )
    monkeypatch.setattr(InventoryJobs, "_observe", lambda self, job: document)
    completed = jobs.run_next()
    assert completed and completed["status"] == "ready"
    request.update(
        version=2, inventory_id=job["inventory_id"], inventory_digest=completed["document_digest"]
    )
    return service, jobs, document, request


def test_inventory_bound_export_and_portable_archive(tmp_path, monkeypatch):
    service, jobs, document, request = inventoried(tmp_path, monkeypatch)
    job = service.prepare(request, export_id="reviewed-export")
    result = service.run(job["export_id"])
    artifact = inspect_package(service.paths.package_directory / job["export_id"])
    assert result["status"] == "captured"
    assert artifact.document["schema_version"] == 2
    assert artifact.document["repositories"][0]["source_native_id"] == "7"
    assert artifact.document["repositories"][0]["origin"] == {"instance_key": "a", "global_id": "7"}
    assert "inventory/document.json" in artifact.files
    report = inventory_report(artifact.directory, artifact.document)
    assert report and report["summary"]["inventory_complete"]
    assert not report["summary"]["full_transfer"]
    # The receiving side gets only files, not source DB or approval state.
    archive = tmp_path / "transfer.zip"
    write_package_archive(artifact.directory, archive)
    destination = tmp_path / "received"
    receive_package_archive(archive, destination)
    received = inspect_package(destination)
    assert received.digest_sha256 == artifact.digest_sha256
    assert inventory_report(destination, received.document) == report
    assert jobs.get(document.snapshot_id)["document"] == document.model_dump(mode="json")


@pytest.mark.parametrize("change", ["scope", "digest", "metadata", "version", "unbound"])
def test_export_rejects_mismatched_review_before_capture(tmp_path, monkeypatch, change):
    service, _, _, request = inventoried(tmp_path, monkeypatch)
    if change == "scope":
        request["repositories"][0]["global_id"] = "8"
    elif change == "digest":
        request["inventory_digest"] = "0" * 64
    elif change == "metadata":
        request["include_metadata"] = False
    elif change == "version":
        request["version"] = True
    else:
        request.pop("inventory_id")
    with pytest.raises(SourceExportConflict):
        service.prepare(request)
    assert service.list() == []


def test_drift_blocks_export_without_replacing_inventory(tmp_path, monkeypatch):
    service, jobs, document, request = inventoried(tmp_path, monkeypatch)
    job = service.prepare(request)
    changed = document.model_copy(deep=True)
    next(c for c in changed.repositories[0].classes if c.content_class == "issues").objects[
        0
    ].facts["body"] = "new source edit"
    monkeypatch.setattr(InventoryJobs, "_observe", lambda self, job: changed)
    with pytest.raises(SourceExportConflict, match="stale"):
        service.run(job["export_id"])
    assert service.get(job["export_id"])["status"] == "failed"
    assert not (service.paths.package_directory / job["export_id"]).exists()
    assert jobs.get(document.snapshot_id)["document"] == document.model_dump(mode="json")


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "installation-marker",
        "repo",
        "organization",
        "team",
        "author",
        "body",
        "comment-parent",
        "digest",
        "version",
    ],
)
def test_v2_inventory_tampering_cannot_pass_package_inspection(tmp_path, monkeypatch, change):
    service, _, _, request = inventoried(tmp_path, monkeypatch)
    job = service.prepare(request)
    service.run(job["export_id"])
    directory = service.paths.package_directory / job["export_id"]
    package = json.loads((directory / "package.json").read_text())
    document = json.loads((directory / "inventory/document.json").read_text())
    repo = document["repositories"][0]
    classes = {c["content_class"]: c for c in repo["classes"]}
    if change == "source":
        document["source_instance_key"] = "unrelated"
    elif change == "installation-marker":
        document["source_installation_marker"] = "rebuilt-source-installation"
    elif change == "repo":
        repo["native_id"] = "99"
    elif change == "organization":
        classes["organizations"]["objects"][0]["native_id"] = "99"
    elif change == "team":
        classes["teams"]["objects"][0]["facts"]["name"] = "other"
    elif change == "author":
        classes["comments"]["objects"][0]["author"]["account_id"] = "99"
    elif change == "body":
        classes["comments"]["objects"][0]["facts"]["body"] = "tampered"
    elif change == "comment-parent":
        classes["comments"]["objects"][0]["parent"]["key"] = "999"
    elif change == "version":
        document["schema_version"] = 99
    raw = (canonical_json(document) + "\n").encode()
    (directory / "inventory/document.json").write_bytes(raw)
    package["inventory"]["size"] = len(raw)
    package["inventory"]["sha256"] = hashlib.sha256(raw).hexdigest()
    package["inventory"]["content_digest"] = hashlib.sha256(
        canonical_json(document).encode()
    ).hexdigest()
    if change == "digest":
        package["inventory"]["sha256"] = "0" * 64
    (directory / "package.json").unlink()
    (directory / "package.sha256").unlink()
    with pytest.raises(PackageError):
        write_package(directory, package)


def test_v1_request_shape_and_package_schema_are_unchanged():
    original = requested()
    assert parse_export_request(copy.deepcopy(original)) == original
