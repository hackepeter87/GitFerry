from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from gitferry.transport import (
    ManifestConflict,
    ManifestValidationError,
    load_transport,
    write_transport,
)


def manifest(*, manifest_id: str = "manifest-forward-1") -> dict[str, object]:
    return {
        "format": "gitferry.transport",
        "schema_version": 1,
        "manifest_id": manifest_id,
        "transfer_round_id": "round-synthetic-1",
        "producer_version": "0.1.0.dev0",
        "sender": {"instance_key": "instance-a", "installation_marker": "install-a"},
        "recipient": {
            "instance_key": "instance-b",
            "installation_marker": "install-b",
            "context": "isolated synthetic integration target",
        },
        "mode": "forward",
        "created_at": "2026-11-20T10:00:00Z",
        "deletion_semantics": "absence-does-not-delete",
        "selected_repositories": [
            {
                "repository": "synthetic/selected",
                "object_types": ["issue"],
                "fields": ["title", "body", "state", "labels"],
                "capabilities": ["issue-create", "issue-update"],
                "limitations": ["native-created-at-not-restored"],
            }
        ],
        "identity_claims": [
            {
                "realm_key": "realm-a",
                "login": "author-a",
                "external_id": "SYNTHETIC-A-1001",
                "ou_path": "OU=People,DC=a,DC=invalid",
                "relationship": {
                    "instance_key": "instance-a",
                    "account_id": "41",
                    "login": "author-a",
                    "status": "observed-not-approved",
                },
            }
        ],
        "objects": [
            {
                "origin": {
                    "instance_key": "instance-a",
                    "repository": "synthetic/selected",
                    "object_type": "issue",
                    "global_id": "9007199254740993",
                    "issue_index": "3",
                    "created_at": "2026-11-20T09:00:00Z",
                },
                "replicas": [
                    {
                        "instance_key": "instance-a",
                        "repository": "synthetic/selected",
                        "global_id": "9007199254740993",
                        "issue_index": "3",
                        "native_created_at": "2026-11-20T09:00:00Z",
                    }
                ],
                "comparison": {
                    "ruleset": "gitea-issue-selected-fields",
                    "ruleset_version": 1,
                    "original_baseline": {
                        "title": "Synthetic original",
                        "body": "Before",
                        "state": "open",
                        "labels": [],
                    },
                    "confirmed_import": None,
                    "current": {
                        "title": "Synthetic original",
                        "body": "Before",
                        "state": "open",
                        "labels": [],
                    },
                    "baseline_origin": {
                        "instance_key": "instance-a",
                        "transfer_round_id": "round-synthetic-1",
                    },
                },
            }
        ],
        "payloads": [],
    }


def test_write_and_load_hash_exact_stored_bytes(tmp_path: Path) -> None:
    artifact = write_transport(tmp_path / "transport", manifest())

    raw = artifact.manifest_path.read_bytes()
    assert artifact.digest_sha256 == hashlib.sha256(raw).hexdigest()
    assert artifact.digest_path.read_text(encoding="ascii").strip() == artifact.digest_sha256
    loaded = load_transport(tmp_path / "transport")
    assert loaded.digest_sha256 == artifact.digest_sha256
    assert loaded.document["objects"][0]["origin"]["global_id"] == "9007199254740993"


def test_same_manifest_is_repeatable_but_id_with_changed_bytes_conflicts(tmp_path: Path) -> None:
    directory = tmp_path / "transport"
    first = write_transport(directory, manifest())
    second = write_transport(directory, manifest())
    assert second.digest_sha256 == first.digest_sha256

    changed = manifest()
    changed["created_at"] = "2026-11-20T10:00:01Z"
    with pytest.raises(ManifestConflict, match="immutable transport"):
        write_transport(directory, changed)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "transport"
    directory.mkdir()
    raw = b'{"format":"gitferry.transport","format":"other","schema_version":1}'
    (directory / "manifest.json").write_bytes(raw)
    (directory / "manifest.json.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(ManifestValidationError, match="duplicate JSON key: format"):
        load_transport(directory)


def test_unknown_version_and_payload_escape_are_rejected(tmp_path: Path) -> None:
    future = manifest()
    future["schema_version"] = 999
    with pytest.raises(ManifestValidationError, match="unsupported transport schema version"):
        write_transport(tmp_path / "future", future)

    escaped = manifest()
    escaped["payloads"] = [
        {"path": "../outside.bundle", "sha256": "a" * 64, "size": 1, "purpose": "git-bundle"}
    ]
    with pytest.raises(ManifestValidationError, match="relative transport path"):
        write_transport(tmp_path / "escaped", escaped)


def test_digest_mismatch_is_rejected_before_manifest_is_trusted(tmp_path: Path) -> None:
    directory = tmp_path / "transport"
    write_transport(directory, manifest())
    raw = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    raw["created_at"] = "2026-11-20T11:00:00Z"
    (directory / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="digest mismatch"):
        load_transport(directory)


def test_manifest_cannot_import_local_approval_or_success_markers(tmp_path: Path) -> None:
    claimed = manifest()
    claimed["locally_approved"] = True
    with pytest.raises(ManifestValidationError, match="Additional properties"):
        write_transport(tmp_path / "approval", claimed)

    claimed = manifest()
    claimed["objects"][0]["operation_status"] = "confirmed"
    with pytest.raises(ManifestValidationError, match="Additional properties"):
        write_transport(tmp_path / "success", claimed)


def test_payload_parent_symlink_cannot_escape_transport(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data = b"not transport-local"
    (outside / "payload.bin").write_bytes(data)
    directory = tmp_path / "transport"
    document = manifest()
    document["payloads"] = [
        {
            "path": "linked/payload.bin",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "purpose": "issue-payload",
        }
    ]
    write_transport(directory, document)
    os.symlink(outside, directory / "linked")

    with pytest.raises(ManifestValidationError, match="symbolic link"):
        load_transport(directory)


def test_excessive_json_nesting_is_rejected_as_validation_error(tmp_path: Path) -> None:
    directory = tmp_path / "transport"
    directory.mkdir()
    raw = ("[" * 100 + "0" + "]" * 100).encode()
    (directory / "manifest.json").write_bytes(raw)
    (directory / "manifest.json.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(ManifestValidationError, match="nesting limit"):
        load_transport(directory)
