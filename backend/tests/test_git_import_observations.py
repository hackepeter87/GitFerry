from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.plan_application import parse_plan_request
from gitferry.runtime import RuntimePaths
from gitferry.state_models import ObservationConflict, OriginKey, StateStoreError
from gitferry.state_store import StateStore


def _state(tmp_path: Path) -> tuple[StateStore, str]:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("a", "A", "https://a.invalid", "install-a")
    state.register_instance("b", "B", "https://b.invalid", "install-b")
    origin = state.register_origin(OriginKey("a", "acme/repo", "repository", "41"))
    state.add_object_mapping(origin.id, "b", "acme/repo", "84")
    with state.transaction() as connection:
        connection.execute(
            "INSERT INTO package_receipts VALUES (?,?,?,?,?,?)",
            (
                "forward-1",
                "a" * 64,
                "b",
                "forward-1.zip",
                '{"package_id":"forward-1"}',
                "2026-01-01T00:00:00Z",
            ),
        )
    return state, origin.id


def test_git_import_observation_is_immutable_and_survives_restart(tmp_path: Path) -> None:
    state, origin_id = _state(tmp_path)
    observed = {"refs/heads/main": "1" * 40, "refs/heads/release": "2" * 40}

    record = state.record_git_import_observation(
        forward_package_id="forward-1",
        forward_package_digest="a" * 64,
        origin_id=origin_id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        ruleset_name="git-ref-return",
        ruleset_version=1,
        observed=observed,
    )
    reopened = StateStore(tmp_path / "state.sqlite3")
    assert (
        reopened.get_git_import_observation(
            forward_package_id="forward-1", origin_id=origin_id, target_instance_key="b"
        )
        == record
    )
    assert record.observed == observed

    assert (
        reopened.record_git_import_observation(
            forward_package_id="forward-1",
            forward_package_digest="a" * 64,
            origin_id=origin_id,
            target_instance_key="b",
            target_repository_scope="acme/repo",
            ruleset_name="git-ref-return",
            ruleset_version=1,
            observed=observed,
        )
        == record
    )
    with pytest.raises(ObservationConflict, match="immutable"):
        reopened.record_git_import_observation(
            forward_package_id="forward-1",
            forward_package_digest="a" * 64,
            origin_id=origin_id,
            target_instance_key="b",
            target_repository_scope="acme/repo",
            ruleset_name="git-ref-return",
            ruleset_version=1,
            observed={"refs/heads/main": "3" * 40},
        )


def test_observation_requires_matching_receipt_and_repository_origin(tmp_path: Path) -> None:
    state, origin_id = _state(tmp_path)
    with pytest.raises(ObservationConflict, match="digest"):
        state.record_git_import_observation(
            forward_package_id="forward-1",
            forward_package_digest="b" * 64,
            origin_id=origin_id,
            target_instance_key="b",
            target_repository_scope="acme/repo",
            ruleset_name="git-ref-return",
            ruleset_version=1,
            observed={},
        )
    with pytest.raises(StateStoreError, match="receipt"):
        state.record_git_import_observation(
            forward_package_id="missing",
            forward_package_digest="a" * 64,
            origin_id=origin_id,
            target_instance_key="b",
            target_repository_scope="acme/repo",
            ruleset_name="git-ref-return",
            ruleset_version=1,
            observed={},
        )
    with pytest.raises(ObservationConflict, match="another target"):
        state.record_git_import_observation(
            forward_package_id="forward-1",
            forward_package_digest="a" * 64,
            origin_id=origin_id,
            target_instance_key="a",
            target_repository_scope="acme/repo",
            ruleset_name="git-ref-return",
            ruleset_version=1,
            observed={},
        )
    with pytest.raises(ValueError, match="valid native Git ref map"):
        state.record_git_import_observation(
            forward_package_id="forward-1",
            forward_package_digest="a" * 64,
            origin_id=origin_id,
            target_instance_key="b",
            target_repository_scope="acme/repo",
            ruleset_name="git-ref-return",
            ruleset_version=1,
            observed={"main": "not-an-oid"},
        )


def test_plan_adapter_freezes_confirmed_forward_import_refs(tmp_path: Path) -> None:
    state, origin_id = _state(tmp_path)
    refs = {"refs/heads/main": "1" * 40}

    class Bundles:
        def _verify_repository_id(self, repository: str, repository_id: str) -> None:
            assert (repository, repository_id) == ("acme/repo", "84")

        def check_remote(self, repository: str, **_: Any) -> dict[str, str]:
            assert repository == "acme/repo"
            return refs

    request = parse_plan_request(
        {
            "version": 1,
            "target_instance_key": "b",
            "round_key": "round-1",
            "package_ref": "forward-1",
            "package_digest": "a" * 64,
            "repositories": [
                {
                    "organization_origin_id": "organization-origin",
                    "organization": "acme",
                    "repository_origin_id": origin_id,
                    "repository": "acme/repo",
                    "bundle": {
                        "member": "repositories/acme/repo.bundle",
                        "sha256": "b" * 64,
                        "size": 1,
                    },
                }
            ],
        }
    )
    paths = RuntimePaths(state.path, tmp_path / "packages", tmp_path / "work")
    adapter = GiteaPlanAdapter(state, paths)
    adapter.request = request
    adapter.forward_package = ("forward-1", "a" * 64)
    adapter.bundles = Bundles()  # type: ignore[assignment]

    adapter._record_git_import_observation(request.repositories[0])

    assert (
        state.get_git_import_observation(
            forward_package_id="forward-1", origin_id=origin_id, target_instance_key="b"
        ).observed
        == refs
    )


def test_direct_plan_does_not_require_portable_package_evidence(tmp_path: Path) -> None:
    state, _ = _state(tmp_path)
    request = parse_plan_request(
        {
            "version": 1,
            "target_instance_key": "b",
            "round_key": "round-1",
            "package_ref": "manual-bundle",
            "repositories": [
                {
                    "organization_origin_id": "organization-origin",
                    "organization": "acme",
                    "repository_origin_id": "repository-origin",
                    "repository": "acme/repo",
                    "bundle": {
                        "member": "repositories/acme/repo.bundle",
                        "sha256": "b" * 64,
                        "size": 1,
                    },
                }
            ],
        }
    )
    adapter = GiteaPlanAdapter(
        state,
        RuntimePaths(state.path, tmp_path / "packages", tmp_path / "work"),
    )
    adapter.request = request

    adapter._record_git_import_observation(request.repositories[0])

    assert adapter.forward_package is None
