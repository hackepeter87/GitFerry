from __future__ import annotations

import hashlib
from pathlib import Path

from gitferry.application_package import write_package
from gitferry.package_selection import build_package_selection
from gitferry.state_codec import origin_id
from gitferry.state_store import OriginKey, StateStore
from test_application_package import selection
from test_plan_application import configured_plan


def test_structure_scopes_and_members_do_not_consume_author_credentials(tmp_path: Path) -> None:
    state, _ = configured_plan(tmp_path)
    document = {
        "format": "gitferry.application-package",
        "schema_version": 1,
        "package_id": "selection-structure",
        "transfer_round_id": "round-structure",
        "producer_version": "test",
        "created_at": "2026-11-21T12:00:00Z",
        "mode": "first-import",
        "sender": {"instance_key": "source", "installation_marker": "install-source"},
        "recipient": {"instance_key": "target", "installation_marker": "install-target"},
        "repositories": [
            {
                "origin": {"instance_key": "source", "global_id": "51"},
                "repository": "org/repo",
                "organization": {
                    "origin": {"instance_key": "source", "global_id": "50"},
                    "name": "org",
                },
                "teams": [
                    {
                        "origin": {"instance_key": "source", "global_id": "52"},
                        "name": "writers",
                        "permission": "write",
                        "members": [
                            {
                                "realm_key": "source-realm",
                                "login": "issue-author",
                                "external_id": "external-2",
                                "ou_path": "OU=Source",
                            }
                        ],
                    }
                ],
                "bundle": None,
            }
        ],
        "realms": [{"realm_key": "source-realm", "kind": "external", "description": "synthetic"}],
        "metadata": None,
        "inventory_complete": False,
        "limitations": ["synthetic"],
        "deletion_semantics": "absence-does-not-delete",
    }
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    package = write_package(package_dir, document)
    result = build_package_selection(
        state,
        package,
        "package",
        include_metadata=False,
        apply_members=True,
        author_credential_refs={},
    )
    request = result["request"]
    assert request["repositories"][0]["organization_origin_id"] == origin_id(
        "source", "org", "organization", "50"
    )
    assert request["repositories"][0]["teams"][0]["origin_id"] == origin_id(
        "source", "org", "team", "52"
    )
    assert request["repositories"][0]["teams"][0]["members"] == [
        {"binding_id": "issue-author", "role": "write"}
    ]
    assert result["missing_credentials"] == []


def test_selection_is_read_only_and_reports_unknown_operations(tmp_path: Path) -> None:
    state, _ = configured_plan(tmp_path)
    document = selection()
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    package = write_package(package_dir, document)
    repo_origin = origin_id("source", "synthetic/selected", "repository", "9007199254740993")
    state.register_origin(
        OriginKey("source", "synthetic/selected", "repository", "9007199254740993")
    )
    operation = state.prepare_operation(
        operation_id="unknown-selection-operation",
        origin_id=repo_origin,
        target_instance_key="target",
        target_repository_scope="synthetic/selected",
        operation_type="create_repository",
        mapping_version=1,
        baseline_id=None,
        intent={"name": "selected"},
    )
    with state.transaction() as connection:
        connection.execute(
            "UPDATE operations SET status='outcome_unknown', error_reason='synthetic' "
            "WHERE operation_id=?",
            (operation.operation_id,),
        )
    before = state.path.read_bytes()
    result = build_package_selection(
        state,
        package,
        "package",
        include_metadata=False,
        apply_members=False,
        author_credential_refs={},
    )
    assert result["blocked_operations"] == ["unknown-selection-operation"]
    assert state.path.read_bytes() == before


def test_return_selection_defers_existing_branch_ancestry_to_bundle_preflight(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    state.register_instance("a", "A", "https://a.invalid", "install-a")
    state.register_instance("b", "B", "https://b.invalid", "install-b")
    origin = state.register_origin(OriginKey("a", "org/repo", "repository", "41"))
    state.add_object_mapping(origin.id, "a", "org/repo", "41")
    payload = b"synthetic return bundle"
    document = {
        "format": "gitferry.application-package",
        "schema_version": 5,
        "package_id": "return-existing-branch",
        "transfer_round_id": "round-existing-branch",
        "producer_version": "test",
        "created_at": "2026-11-27T08:00:00Z",
        "mode": "return",
        "sender": {"instance_key": "b", "installation_marker": "install-b"},
        "recipient": {"instance_key": "a", "installation_marker": "install-a"},
        "forward_reference": {"package_id": "forward", "digest_sha256": "a" * 64},
        "repositories": [
            {
                "origin": {"instance_key": "a", "global_id": "41"},
                "repository": "org/repo",
                "bundle": {
                    "path": "bundles/return.bundle",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                },
                "comparison": {
                    "ruleset": "git-refs",
                    "ruleset_version": 1,
                    "original_baseline": {"refs/heads/main": "1" * 40},
                    "confirmed_import": {"refs/heads/main": "1" * 40},
                    "desired": {"refs/heads/main": "2" * 40},
                },
            }
        ],
        "limitations": ["No deletion"],
        "deletion_semantics": "absence-does-not-delete",
    }
    package_dir = tmp_path / "package"
    (package_dir / "bundles").mkdir(parents=True)
    (package_dir / "bundles/return.bundle").write_bytes(payload)
    artifact = write_package(package_dir, document)

    accepted = build_package_selection(
        state,
        artifact,
        "package",
        include_metadata=False,
        apply_members=False,
        author_credential_refs={},
        return_baselines={"org/repo": {"refs/heads/main": "1" * 40}},
    )
    assert accepted["return_conflicts"] == []
    assert accepted["request"]["repositories"][0]["return_comparison"]["desired"] == {
        "refs/heads/main": "2" * 40
    }

    mismatched = build_package_selection(
        state,
        artifact,
        "package",
        include_metadata=False,
        apply_members=False,
        author_credential_refs={},
        return_baselines={"org/repo": {"refs/heads/main": "3" * 40}},
    )
    assert {item["code"] for item in mismatched["return_conflicts"]} == {"baseline-mismatch"}
