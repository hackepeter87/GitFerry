from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from gitferry.application_package import PackageError, inspect_package, write_package
from gitferry.cli import main
from gitferry.package_archive import receive_package_archive, write_package_archive

ROOT = Path(__file__).resolve().parents[2]


def selection() -> dict[str, Any]:
    return {
        "format": "gitferry.application-package",
        "schema_version": 1,
        "package_id": "synthetic-package-1",
        "transfer_round_id": "synthetic-round-1",
        "producer_version": "0.1.0.dev0",
        "created_at": "2026-11-21T12:00:00Z",
        "mode": "first-import",
        "sender": {"instance_key": "source", "installation_marker": "source-install-1"},
        "recipient": {"instance_key": "target", "installation_marker": "target-install-1"},
        "repositories": [
            {
                "origin": {"instance_key": "source", "global_id": "9007199254740993"},
                "repository": "synthetic/selected",
                "organization": {
                    "origin": {"instance_key": "source", "global_id": "42"},
                    "name": "synthetic",
                },
                "teams": [],
                "bundle": None,
            }
        ],
        "realms": [],
        "metadata": None,
        "inventory_complete": False,
        "limitations": ["Content inventory is incomplete; receipt authorizes no writes."],
        "deletion_semantics": "absence-does-not-delete",
    }


def with_metadata(directory: Path, version: int) -> tuple[dict[str, Any], bytes]:
    document = selection()
    raw = (ROOT / f"examples/transport-forward.v{version}.json").read_bytes()
    manifest = json.loads(raw)
    metadata = directory / "metadata"
    metadata.mkdir(parents=True)
    (metadata / "manifest.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (metadata / "manifest.json.sha256").write_text(digest + "\n")
    document["sender"] = manifest["sender"]
    document["recipient"] = {
        key: manifest["recipient"][key] for key in ("instance_key", "installation_marker")
    }
    document["transfer_round_id"] = manifest["transfer_round_id"]
    document["realms"] = [
        {
            "realm_key": "synthetic-directory-a",
            "kind": "external",
            "description": "Synthetic realm, no directory integration claim",
        }
    ]
    author = {
        "realm_key": "synthetic-directory-a",
        "login": "issue-author",
        "external_id": "SYNTHETIC-ISSUE-1",
        "ou_path": "OU=People,DC=a,DC=invalid",
    }
    document["metadata"] = {
        "directory": "metadata",
        "manifest_id": manifest["manifest_id"],
        "digest_sha256": digest,
        "issue_authors": [
            {
                "origin": {
                    key: item["origin"][key]
                    for key in ("instance_key", "repository", "object_type", "global_id")
                },
                "author": author,
            }
            for item in manifest["objects"]
            if item["origin"]["object_type"] == "issue"
        ],
    }
    return document, raw


@pytest.mark.parametrize("version", [1, 2])
def test_file_roundtrip_preserves_existing_manifest_bytes_and_author_relation(
    tmp_path: Path, version: int
) -> None:
    source = tmp_path / "source"
    document, raw = with_metadata(source, version)
    original = write_package(source, document)
    archive = tmp_path / "transfer.zip"
    write_package_archive(source, archive)
    destination = tmp_path / "received"
    received = receive_package_archive(archive, destination)
    assert received.digest_sha256 == original.digest_sha256
    assert (destination / "metadata/manifest.json").read_bytes() == raw
    assert received.document["metadata"]["issue_authors"][0]["author"]["login"] == "issue-author"
    assert received.document["repositories"][0]["origin"]["global_id"] == "9007199254740993"
    # Independent process only receives the portable files; it needs no source DB or credentials.
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            "from gitferry.cli import main; import sys; sys.exit(main())",
            "packages",
            "verify",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "validated-files-only"
    assert report["execution_authorized"] is False
    assert report["inventory_complete"] is False
    assert not list(tmp_path.rglob("*.sqlite3"))


def test_bundle_only_package_and_cli_receipt_need_no_fabricated_metadata(
    tmp_path: Path, capsys: Any
) -> None:
    source = tmp_path / "source"
    (source / "bundles").mkdir(parents=True)
    payload = b"synthetic bundle payload\x00\xff"
    (source / "bundles/selected.bundle").write_bytes(payload)
    document = selection()
    document["repositories"][0]["bundle"] = {
        "path": "bundles/selected.bundle",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    write_package(source, document)
    archive = tmp_path / "transfer.zip"
    assert main(["packages", "pack", str(source), "--output", str(archive)]) == 0
    assert main(["packages", "receive", str(archive), "--output", str(tmp_path / "received")]) == 0
    assert (tmp_path / "received/bundles/selected.bundle").read_bytes() == payload
    assert all(
        json.loads(line)["execution_authorized"] is False
        for line in capsys.readouterr().out.splitlines()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "approval",
        "operation",
        "complete",
        "duplicate-repository",
        "alias-repository",
        "unknown-realm",
        "duplicate-team",
        "wrong-organization",
    ],
)
def test_invalid_descriptor_cannot_be_sealed(tmp_path: Path, mutation: str) -> None:
    document = selection()
    repository = document["repositories"][0]
    if mutation == "version":
        document["schema_version"] = 99
    elif mutation == "approval":
        document["approved"] = True
    elif mutation == "operation":
        document["operations"] = [{"status": "confirmed"}]
    elif mutation == "complete":
        document["inventory_complete"] = True
    elif mutation == "duplicate-repository":
        document["repositories"].append(copy.deepcopy(repository))
    elif mutation == "alias-repository":
        other = copy.deepcopy(repository)
        other["repository"] = "synthetic/other"
        document["repositories"].append(other)
    elif mutation == "wrong-organization":
        repository["organization"]["name"] = "other"
    else:
        team = {
            "origin": {"instance_key": "source", "global_id": "44"},
            "name": "readers",
            "permission": "read",
            "members": [],
        }
        repository["teams"] = [team]
        if mutation == "duplicate-team":
            repository["teams"].append(copy.deepcopy(team))
        else:
            team["members"] = [
                {"realm_key": "unknown", "login": "reader", "external_id": "11", "ou_path": ""}
            ]
    with pytest.raises(PackageError):
        write_package(tmp_path, document)
    assert not (tmp_path / "package.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "sender",
        "recipient",
        "round",
        "digest",
        "missing-author",
        "duplicate-author",
        "unknown-author",
        "outside-selection",
    ],
)
def test_metadata_relationship_conflicts_leave_no_package(tmp_path: Path, mutation: str) -> None:
    document, _ = with_metadata(tmp_path, 2)
    if mutation in ("sender", "recipient"):
        document[mutation]["installation_marker"] = "different-installation"
    elif mutation == "round":
        document["transfer_round_id"] = "another-round"
    elif mutation == "digest":
        document["metadata"]["digest_sha256"] = "0" * 64
    elif mutation == "missing-author":
        document["metadata"]["issue_authors"] = []
    elif mutation == "duplicate-author":
        document["metadata"]["issue_authors"] *= 2
    elif mutation == "unknown-author":
        document["metadata"]["issue_authors"][0]["origin"]["global_id"] = "123"
    else:
        document["repositories"][0]["repository"] = "synthetic/other"
    with pytest.raises(PackageError):
        write_package(tmp_path, document)
    assert not (tmp_path / "package.json").exists()


def test_receipt_rejects_modified_bytes_extra_files_and_duplicate_json_keys(tmp_path: Path) -> None:
    document = selection()
    write_package(tmp_path, document)
    original = (tmp_path / "package.json").read_bytes()
    (tmp_path / "package.json").write_bytes(original + b" ")
    with pytest.raises(PackageError, match="byte digest"):
        inspect_package(tmp_path)
    (tmp_path / "package.json").write_bytes(original)
    (tmp_path / "private.txt").write_text("must not be exported")
    with pytest.raises(PackageError, match="undeclared"):
        inspect_package(tmp_path)
    (tmp_path / "private.txt").unlink()
    repeated = original.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')
    (tmp_path / "package.json").write_bytes(repeated)
    (tmp_path / "package.sha256").write_text(hashlib.sha256(repeated).hexdigest() + "\n")
    with pytest.raises(PackageError, match="bounded JSON"):
        inspect_package(tmp_path)


def test_existing_package_cannot_be_rewritten(tmp_path: Path) -> None:
    document = selection()
    first = write_package(tmp_path, document)
    assert write_package(tmp_path, document).digest_sha256 == first.digest_sha256
    document["package_id"] = "another-id"
    with pytest.raises(PackageError, match="differs"):
        write_package(tmp_path, document)
    assert inspect_package(tmp_path).digest_sha256 == first.digest_sha256


def test_embedded_objects_cannot_escape_declared_selection(tmp_path: Path) -> None:
    document, raw = with_metadata(tmp_path, 2)
    manifest = json.loads(raw)
    for item in manifest["objects"]:
        item["origin"]["repository"] = "outside/unselected"
        if "parent_issue_origin" in item["origin"]:
            item["origin"]["parent_issue_origin"]["repository"] = "outside/unselected"
    document["metadata"]["issue_authors"][0]["origin"]["repository"] = "outside/unselected"
    changed = json.dumps(manifest).encode()
    digest = hashlib.sha256(changed).hexdigest()
    (tmp_path / "metadata/manifest.json").write_bytes(changed)
    (tmp_path / "metadata/manifest.json.sha256").write_text(digest + "\n")
    document["metadata"]["digest_sha256"] = digest
    with pytest.raises(PackageError, match="object origin is outside"):
        write_package(tmp_path, document)
    assert not (tmp_path / "package.json").exists()
