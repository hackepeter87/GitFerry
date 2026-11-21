from __future__ import annotations

from pathlib import Path

from gitferry.application_package import write_package
from gitferry.package_selection import build_package_selection
from gitferry.state_codec import origin_id
from gitferry.state_store import OriginKey
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
