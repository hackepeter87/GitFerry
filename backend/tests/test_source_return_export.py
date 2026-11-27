from __future__ import annotations

import hashlib
import json
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gitferry.source_exports as exports_module
from gitferry.application_package import inspect_package, write_package
from gitferry.content_inventory import CONTENT_CLASSES, export_classes, parse_inventory
from gitferry.package_inventory import attach_inventory
from gitferry.runtime import RuntimePaths
from gitferry.source_export_state import record_source_capture
from gitferry.source_exports import SourceExports
from gitferry.state_codec import canonical_json
from gitferry.state_store import OriginKey, StateStore


def _forward_package(state: StateStore, paths: RuntimePaths) -> tuple[str, str, str]:
    package_id = "forward-1"
    package_ref = "received-forward-archive"
    directory = paths.package_directory / package_ref
    directory.mkdir()
    payload = b"synthetic forward bundle"
    bundle = directory / "bundles/forward.bundle"
    bundle.parent.mkdir()
    bundle.write_bytes(payload)
    package = json.loads(
        (Path(__file__).resolve().parents[2] / "examples/application-package.v1.json").read_text()
    )
    package.update(
        package_id=package_id,
        transfer_round_id="round-1",
        created_at="2026-11-27T09:00:00Z",
        sender={"instance_key": "a", "installation_marker": "install-a"},
        recipient={"instance_key": "b", "installation_marker": "install-b"},
    )
    selected = package["repositories"][0]
    selected["origin"] = {"instance_key": "a", "global_id": "7"}
    selected["organization"]["origin"] = {"instance_key": "a", "global_id": "3"}
    selected["bundle"] = {
        "path": "bundles/forward.bundle",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    requested = export_classes(metadata=False, members=False)
    observations: dict[str, list[dict[str, Any]]] = {
        "git_refs": [
            {
                "key": "refs/heads/main",
                "namespace": "git-ref",
                "facts": {"ref": "refs/heads/main", "sha": "1" * 40},
            }
        ],
        "organizations": [
            {
                "key": "3",
                "native_id": "3",
                "namespace": "organization-id",
                "facts": {"name": "synthetic"},
            }
        ],
        "teams": [],
        "permissions": [],
    }
    inventory = parse_inventory(
        {
            "format": "gitferry.content-inventory",
            "schema_version": 1,
            "comparison_version": 1,
            "snapshot_id": "inventory-forward-1",
            "created_at": "2026-11-27T09:00:00Z",
            "source_instance_key": "a",
            "source_installation_marker": "install-a",
            # Content-inventory v1 is byte-compatible with its historical fixture.
            "gitea_version": "1.27.2",
            "deletion_semantics": "absence-does-not-delete",
            "repositories": [
                {
                    "repository": "synthetic/selected",
                    "native_id": "7",
                    "requested_classes": requested,
                    "classes": [
                        {
                            "content_class": name,
                            "status": "observed" if name in requested else "excluded",
                            "count": len(observations.get(name, [])) if name in requested else None,
                            "objects": observations.get(name, []),
                            "reason": "" if name in requested else "Outside selected scope",
                        }
                        for name in CONTENT_CLASSES
                    ],
                }
            ],
        }
    )
    snapshot = {
        "repositories": [
            {
                "repository": "synthetic/selected",
                "global_id": "7",
                "organization": {"global_id": "3"},
                "teams": [],
            }
        ]
    }
    attach_inventory(directory, package, inventory, snapshot, include_members=False)
    artifact = write_package(directory, package)
    origin = state.register_origin(OriginKey("a", "synthetic/selected", "repository", "7"))
    state.add_object_mapping(origin.id, "b", "synthetic/selected", "84")
    with state.transaction() as connection:
        connection.execute(
            "INSERT INTO package_receipts VALUES (?,?,?,?,?,?)",
            (
                package_id,
                artifact.digest_sha256,
                "b",
                package_ref,
                canonical_json(artifact.document),
                "2026-11-27T09:01:00Z",
            ),
        )
    state.record_git_import_observation(
        forward_package_id=package_id,
        forward_package_digest=artifact.digest_sha256,
        origin_id=origin.id,
        target_instance_key="b",
        target_repository_scope="synthetic/selected",
        ruleset_name="git-ref-return",
        ruleset_version=1,
        observed={"refs/heads/main": "1" * 40},
    )
    return artifact.digest_sha256, origin.id, package_id


def test_return_capture_uses_only_forward_and_confirmed_local_facts(
    tmp_path: Path, monkeypatch
) -> None:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("a", "Main", "https://a.invalid", "install-a")
    state.register_instance("b", "Offline", "https://b.invalid", "install-b")
    paths = RuntimePaths(state.path, tmp_path / "packages", tmp_path / "work")
    paths.package_directory.mkdir()
    paths.work_directory.mkdir()
    digest, _, package_id = _forward_package(state, paths)

    class ReturnGit:
        def __init__(self, package_root: Path, *_: Any, **__: Any):
            self.package_root = package_root

        def __enter__(self) -> ReturnGit:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def export_bundle(self, _: str, *, member: str, **__: Any) -> SimpleNamespace:
            path = self.package_root / member
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = b"synthetic return bundle"
            path.write_bytes(payload)
            return SimpleNamespace(
                member=member,
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )

        def check_remote(self, *_: Any, **__: Any) -> dict[str, str]:
            return {"refs/heads/main": "2" * 40}

    monkeypatch.setattr(exports_module, "GiteaBundlePusher", ReturnGit)
    service = SourceExports(state, paths)
    work = paths.work_directory / "return-1"
    work.mkdir()
    artifact = service._capture_return(
        {
            "export_id": "return-1",
            "created_at": "2026-11-27T09:02:00Z",
            "request": {
                "version": 5,
                "mode": "return",
                "forward_package_id": package_id,
                "forward_package_digest": digest,
                "source_instance_key": "b",
                "recipient_instance_key": "a",
                "repositories": [{"repository": "synthetic/selected", "global_id": "84"}],
            },
        },
        work,
        "https://b.invalid",
        "technical",
        "synthetic-token",
        ssl.create_default_context(),
    )

    received = inspect_package(artifact.directory)
    comparison = received.document["repositories"][0]["comparison"]
    assert comparison["original_baseline"] == {"refs/heads/main": "1" * 40}
    assert comparison["confirmed_import"] == {"refs/heads/main": "1" * 40}
    assert comparison["desired"] == {"refs/heads/main": "2" * 40}
    assert received.document["forward_reference"] == {
        "package_id": package_id,
        "digest_sha256": digest,
    }

    request = {
        "version": 5,
        "mode": "return",
        "forward_package_id": package_id,
        "forward_package_digest": digest,
        "source_instance_key": "b",
        "recipient_instance_key": "a",
        "repositories": [{"repository": "synthetic/selected", "global_id": "84"}],
        "include_metadata": False,
        "include_members": False,
    }
    with state.transaction() as connection:
        connection.execute(
            "INSERT INTO source_exports VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "return-1",
                "f" * 64,
                canonical_json(request),
                "{}",
                "capturing",
                received.digest_sha256,
                None,
                None,
                "2026-11-27T09:02:00Z",
                "2026-11-27T09:02:00Z",
            ),
        )

    record_source_capture(state, received, "return-1")

    with state.read_connection() as connection:
        row = connection.execute(
            "SELECT status,package_digest FROM source_exports WHERE export_id='return-1'"
        ).fetchone()
    assert (row["status"], row["package_digest"]) == ("captured", received.digest_sha256)
