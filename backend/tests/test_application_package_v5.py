from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from gitferry.application_package import PackageError, inspect_package, write_package

ROOT = Path(__file__).resolve().parents[2]


def return_package() -> dict[str, Any]:
    return {
        "format": "gitferry.application-package",
        "schema_version": 5,
        "package_id": "return-package-1",
        "transfer_round_id": "round-1",
        "producer_version": "0.3.0rc1",
        "created_at": "2026-11-27T08:00:00Z",
        "mode": "return",
        "sender": {"instance_key": "target", "installation_marker": "target-install-1"},
        "recipient": {"instance_key": "source", "installation_marker": "source-install-1"},
        "forward_reference": {
            "package_id": "forward-package-1",
            "digest_sha256": "a" * 64,
        },
        "repositories": [
            {
                "origin": {"instance_key": "source", "global_id": "9007199254740993"},
                "repository": "synthetic/selected",
                "bundle": None,
                "comparison": {
                    "ruleset": "git-refs",
                    "ruleset_version": 1,
                    "original_baseline": {
                        "refs/heads/main": "1" * 40,
                    },
                    "confirmed_import": {
                        "refs/heads/main": "1" * 40,
                    },
                    "desired": {
                        "refs/heads/main": "2" * 40,
                        "refs/heads/offline": "3" * 40,
                    },
                },
            }
        ],
        "limitations": ["No ref deletion or force update"],
        "deletion_semantics": "absence-does-not-delete",
    }


def test_v5_return_package_roundtrips_without_network_or_state(tmp_path: Path) -> None:
    document = return_package()
    bundle = tmp_path / "bundles" / "return.bundle"
    bundle.parent.mkdir()
    payload = b"synthetic return bundle\x00\xff"
    bundle.write_bytes(payload)
    document["repositories"][0]["bundle"] = {
        "path": "bundles/return.bundle",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    artifact = write_package(tmp_path, document)
    received = inspect_package(tmp_path)

    assert received.digest_sha256 == artifact.digest_sha256
    assert received.document["mode"] == "return"
    assert received.document["forward_reference"]["package_id"] == "forward-package-1"
    assert received.files["bundles/return.bundle"] == (
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value.update({"schema_version": 4}), "invalid or unsupported"),
        (
            lambda value: value["forward_reference"].update({"digest_sha256": "b" * 64}),
            "existing package descriptor differs",
        ),
        (
            lambda value: value["repositories"].append(copy.deepcopy(value["repositories"][0])),
            "duplicate selected repository",
        ),
        (
            lambda value: value["repositories"][0]["comparison"]["desired"].update(
                {"heads/main": "4" * 40}
            ),
            "invalid or unsupported",
        ),
    ],
)
def test_v5_rejects_tampered_or_ambiguous_return_package(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    document = return_package()
    write_package(tmp_path, document)
    mutation(document)
    with pytest.raises(PackageError, match=message):
        write_package(tmp_path, document)


def test_v5_rejects_self_forward_reference(tmp_path: Path) -> None:
    document = return_package()
    document["forward_reference"]["package_id"] = document["package_id"]
    with pytest.raises(PackageError, match="reference itself"):
        write_package(tmp_path, document)


def test_v5_rejects_git_invalid_ref_names(tmp_path: Path) -> None:
    document = return_package()
    document["repositories"][0]["comparison"]["desired"] = {"refs/heads/invalid..name": "4" * 40}
    with pytest.raises(PackageError, match="invalid ref"):
        write_package(tmp_path, document)


def test_v5_rejects_undeclared_extra_file(tmp_path: Path) -> None:
    write_package(tmp_path, return_package())
    (tmp_path / "unexpected.txt").write_text("not declared", encoding="utf-8")
    with pytest.raises(PackageError, match="undeclared"):
        inspect_package(tmp_path)


def test_existing_v1_fixture_remains_unchanged(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "examples/application-package.v1.json"
    original = source.read_bytes()
    document = json.loads(original)
    document["package_id"] = "fixture-copy"
    document["transfer_round_id"] = "fixture-round"
    document["created_at"] = "2026-11-27T08:00:00Z"
    artifact = write_package(tmp_path, document)
    assert artifact.document["schema_version"] == 1


def test_v5_return_example_is_a_valid_files_only_carrier(tmp_path: Path) -> None:
    document = json.loads((ROOT / "examples/application-package-return.v5.json").read_bytes())
    artifact = write_package(tmp_path, document)
    assert artifact.document["mode"] == "return"
