from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from gitferry.application_package import PackageError, write_package
from gitferry.connection_probe import approve_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.operations import OperationExecutor
from gitferry.package_admission import PackageAdmissionConflict, PackageAdmissions
from gitferry.package_archive import receive_package_archive, write_package_archive
from gitferry.package_selection import build_package_selection
from gitferry.plan_application import parse_plan_request
from gitferry.runtime import RuntimePaths
from gitferry.state_migrations import MIGRATIONS, SCHEMA_VERSION
from gitferry.state_store import OriginKey, StateStore
from gitferry.transfer_plans import PlanConflict
from gitferry.transport import ManifestConflict
from test_source_exports import configure, mock_reads, requested


def received(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PackageAdmissions, str]:
    source = tmp_path / "source"
    source.mkdir()
    exporter = configure(source)
    mock_reads(monkeypatch)
    job = exporter.prepare(requested())
    exporter.run(job["export_id"])
    archive = tmp_path / "transfer.tar"
    write_package_archive(source / "packages" / job["export_id"], archive)
    target = tmp_path / "target"
    target.mkdir()
    paths = RuntimePaths(target / "state.sqlite", target / "packages", target / "work")
    paths.package_directory.mkdir()
    paths.work_directory.mkdir()
    receive_package_archive(archive, paths.package_directory / "received")
    state = StateStore(paths.state_database)
    state.register_instance("a", "Source registration", "https://source.invalid", "install-a")
    profiles = ProfileService(state)
    profiles.create(
        ConnectionProfile("b", "Target", "https://recipient.invalid", "install-b", "admin")
    )
    identity = json.dumps(
        {
            "account_id": "1",
            "login": "technical",
            "ca_digest": "system-trust",
            "certificate_sha256": "cert",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with state.transaction() as connection:
        connection.execute(
            "INSERT INTO connection_checks VALUES (?,?,?,?,?)",
            ("b", 1, identity, identity, "now"),
        )
    approve_connection(
        profiles,
        "b",
        expected_revision=1,
        evidence_digest=hashlib.sha256(identity.encode()).hexdigest(),
    )
    return PackageAdmissions(state, paths), "received"


def counts(service: PackageAdmissions) -> dict[str, int]:
    with service.state.read_connection() as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in (
                "object_origins",
                "object_mappings",
                "identity_bindings",
                "realms",
                "baselines",
                "operations",
                "transport_manifests",
                "transport_identity_claims",
                "package_receipts",
            )
        }


def admit(service: PackageAdmissions, ref: str) -> dict[str, Any]:
    review = service.review(ref, target_instance_key="b", profile_revision=1)
    return service.admit(
        ref, target_instance_key="b", profile_revision=1, expected_digest=review["digest_sha256"]
    )


def bind_authors(service: PackageAdmissions, ref: str) -> dict[str, str]:
    artifact = service.artifact(ref)
    manifest = json.loads((artifact.directory / "metadata/manifest.json").read_text())
    for realm in artifact.document["realms"]:
        service.state.register_realm(
            realm["realm_key"], "Locally accepted source realm", "synthetic"
        )
    result = {}
    for index, claim in enumerate(manifest["identity_claims"], 20):
        login = claim["login"]
        service.state.confirm_identity_binding(
            binding_id=login,
            source_realm_key=claim["realm_key"],
            source_login=login,
            source_external_id=claim["external_id"],
            source_ou_path=claim["ou_path"],
            target_instance_key="b",
            target_account_id=index,
            target_login=login,
            mapping_version=1,
        )
        result[login] = login + "-credential"
    return result


def test_review_rolls_back_and_receipt_preserves_facts_without_local_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    before = counts(service)
    review = service.plan_review(ref, target_instance_key="b", profile_revision=1)
    assert counts(service) == before
    assert len(review["selection"]["missing_bindings"]) == 2
    report = admit(service, ref)
    assert report["status"] == "admitted-facts-only" and not report["execution_authorized"]
    after = counts(service)
    assert after["object_origins"] == 5 and after["package_receipts"] == 1
    assert after["transport_manifests"] == 1 and after["transport_identity_claims"] == 2
    assert after["identity_bindings"] == after["realms"] == after["operations"] == 0
    assert after["baselines"] == 0  # Original comparison facts stay in the received manifest.
    with service.state.read_connection() as connection:
        comment = connection.execute(
            "SELECT * FROM object_origins WHERE object_type='comment'"
        ).fetchone()
        parent = connection.execute(
            "SELECT * FROM object_origins WHERE origin_id=?", (comment["parent_origin_id"],)
        ).fetchone()
        assert parent["origin_global_id"] == "101" and parent["origin_instance_key"] == "a"
        assert not connection.execute(
            "SELECT 1 FROM object_mappings WHERE target_instance_key='b'"
        ).fetchone()
    assert admit(service, ref)["replay"]
    assert counts(service) == after


def test_return_package_admission_requires_forward_receipt_and_existing_replica(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    state.register_instance("a", "A", "https://a.invalid", "a-install")
    state.register_instance("b", "B", "https://b.invalid", "b-install")
    profiles = ProfileService(state)
    profiles.create(ConnectionProfile("a", "A", "https://a.invalid", "a-install", "admin"))
    identity = json.dumps(
        {
            "account_id": "1",
            "login": "admin",
            "ca_digest": "system-trust",
            "certificate_sha256": "cert",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with state.transaction() as connection:
        connection.execute(
            "INSERT INTO connection_checks VALUES (?,?,?,?,?)",
            ("a", 1, identity, identity, "now"),
        )
    approve_connection(
        profiles,
        "a",
        expected_revision=1,
        evidence_digest=hashlib.sha256(identity.encode()).hexdigest(),
    )
    origin = state.register_origin(OriginKey("a", "org/repo", "repository", "41"))
    state.add_object_mapping(origin.id, "a", "org/repo", "41")
    with state.transaction() as connection:
        connection.execute(
            "INSERT INTO package_receipts VALUES (?,?,?,?,?,?)",
            ("forward-package-1", "a" * 64, "b", "forward", "{}", "now"),
        )
    package = {
        "format": "gitferry.application-package",
        "schema_version": 5,
        "package_id": "return-package-1",
        "transfer_round_id": "round-1",
        "producer_version": "test",
        "created_at": "2026-11-27T08:00:00Z",
        "mode": "return",
        "sender": {"instance_key": "b", "installation_marker": "b-install"},
        "recipient": {"instance_key": "a", "installation_marker": "a-install"},
        "forward_reference": {"package_id": "forward-package-1", "digest_sha256": "a" * 64},
        "repositories": [
            {
                "origin": {"instance_key": "a", "global_id": "41"},
                "repository": "org/repo",
                "bundle": {
                    "path": "bundles/return.bundle",
                    "size": len(b"return bundle"),
                    "sha256": hashlib.sha256(b"return bundle").hexdigest(),
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
    package_dir = tmp_path / "packages" / "return"
    package_dir.mkdir(parents=True)
    (package_dir / "bundles").mkdir()
    (package_dir / "bundles/return.bundle").write_bytes(b"return bundle")
    write_package(package_dir, package)
    paths = RuntimePaths(tmp_path / "runtime.sqlite", tmp_path / "packages", tmp_path / "work")
    paths.package_directory.mkdir(exist_ok=True)
    paths.work_directory.mkdir(exist_ok=True)
    service = PackageAdmissions(state, paths)
    # The package is stored directly under the owned package root for this focused test.
    report = service.review("return", target_instance_key="a", profile_revision=1)
    assert report["replay"] is False
    assert report["forward_reference"]["package_id"] == "forward-package-1"
    selection = service.plan_review(
        "return", target_instance_key="a", profile_revision=1, include_metadata=False
    )["selection"]
    request = parse_plan_request(selection["request"])
    assert request.mode == "return"
    assert request.repositories[0].return_repository_id == "41"
    assert request.repositories[0].return_comparison is not None


def test_receipt_replay_survives_real_process_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    report = admit(service, ref)
    code = """
import json, sys
from pathlib import Path
from gitferry.package_admission import PackageAdmissions
from gitferry.runtime import RuntimePaths
from gitferry.state_store import StateStore
root=Path(sys.argv[1])
paths=RuntimePaths(root/'state.sqlite',root/'packages',root/'work')
service=PackageAdmissions(StateStore(paths.state_database), paths)
print(json.dumps(service.admit('received',target_instance_key='b',profile_revision=1,expected_digest=sys.argv[2])))
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, str(tmp_path / "target"), report["digest_sha256"]],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["replay"]
    assert counts(service)["package_receipts"] == 1
    assert counts(service)["operations"] == 0


def test_failed_receipt_transaction_leaves_no_half_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    digest = service.artifact(ref).digest_sha256
    with service.state.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_receipt BEFORE INSERT ON package_receipts "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    before = counts(service)
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        service.admit(ref, target_instance_key="b", profile_revision=1, expected_digest=digest)
    assert counts(service) == before


@pytest.mark.parametrize(
    "failure", ["wrong-target", "stale-profile", "changed-digest", "known-scope"]
)
def test_untrusted_or_stale_admission_cannot_change_local_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    if failure == "known-scope":
        service.state.register_origin(OriginKey("a", "previous-name", "organization", "3"))
    before = counts(service)
    with pytest.raises((PackageAdmissionConflict, PlanConflict)):
        service.admit(
            ref,
            target_instance_key="a" if failure == "wrong-target" else "b",
            profile_revision=2 if failure == "stale-profile" else 1,
            expected_digest="0" * 64
            if failure == "changed-digest"
            else service.artifact(ref).digest_sha256,
        )
    assert counts(service) == before


def test_missing_bindings_block_plan_without_fallback_or_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    admit(service, ref)
    review = service.plan_review(ref, target_instance_key="b", profile_revision=1)
    with pytest.raises(PackageAdmissionConflict, match="prerequisites"):
        service.prepare_plan(
            ref,
            target_instance_key="b",
            profile_revision=1,
            expected_review_digest=review["review_digest"],
        )
    assert counts(service)["operations"] == counts(service)["identity_bindings"] == 0


def test_explicit_bindings_produce_existing_durable_plan_with_parent_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    admit(service, ref)
    refs = bind_authors(service, ref)
    options = dict(target_instance_key="b", profile_revision=1, author_credential_refs=refs)
    review = service.plan_review(ref, **options)
    assert not any(
        review["selection"][key]
        for key in (
            "missing_bindings",
            "missing_credentials",
            "waiting_parents",
            "blocked_operations",
        )
    )
    plan = service.prepare_plan(ref, expected_review_digest=review["review_digest"], **options)
    assert plan["status"] == "draft"
    with service.state.read_connection() as connection:
        rows = connection.execute("SELECT operation_type,status FROM operations").fetchall()
        assert any(
            row["operation_type"] == "create_comment" and row["status"] == "blocked_parent"
            for row in rows
        )
    replay = service.plan_review(ref, **options)
    assert not replay["selection"]["blocked_operations"]
    assert (
        service.prepare_plan(ref, expected_review_digest=replay["review_digest"], **options)[
            "plan_id"
        ]
        == plan["plan_id"]
    )


def test_structure_only_selection_needs_no_fabricated_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    admit(service, ref)
    options = dict(target_instance_key="b", profile_revision=1, include_metadata=False)
    review = service.plan_review(ref, **options)
    assert not review["selection"]["missing_bindings"]
    assert review["selection"]["request"]["metadata"] == []
    plan = service.prepare_plan(ref, expected_review_digest=review["review_digest"], **options)
    assert plan["status"] == "draft"
    assert counts(service)["identity_bindings"] == 0


def test_changed_selection_requires_new_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    admit(service, ref)
    review = service.plan_review(ref, target_instance_key="b", profile_revision=1)
    with pytest.raises(PackageAdmissionConflict, match="review it again"):
        service.prepare_plan(
            ref,
            target_instance_key="b",
            profile_revision=1,
            include_metadata=False,
            expected_review_digest=review["review_digest"],
        )
    assert counts(service)["operations"] == 0


def repack(service: PackageAdmissions, ref: str, *, new_id: bool) -> str:
    artifact = service.artifact(ref)
    destination = service.paths.package_directory / "repacked"
    shutil.copytree(artifact.directory, destination)
    (destination / "package.json").unlink()
    (destination / "package.sha256").unlink()
    document = artifact.document
    document["limitations"].append("Explicitly repackaged synthetic selection")
    if new_id:
        document["package_id"] = "repackaged-selection"
    write_package(destination, document)
    return "repacked"


def test_same_package_id_cannot_identify_changed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    admit(service, ref)
    before = counts(service)
    renamed = repack(service, ref, new_id=False)
    with pytest.raises(PackageAdmissionConflict, match="different received bytes"):
        admit(service, renamed)
    assert counts(service) == before


def test_repackaging_preserves_unknown_remote_operation_and_blocks_new_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    admit(service, ref)
    refs = bind_authors(service, ref)
    options = dict(target_instance_key="b", profile_revision=1, author_credential_refs=refs)
    review = service.plan_review(ref, **options)
    service.prepare_plan(ref, expected_review_digest=review["review_digest"], **options)
    with service.state.read_connection() as connection:
        identifier = connection.execute(
            "SELECT operation_id FROM operations WHERE operation_type='create_issue'"
        ).fetchone()[0]
    with OperationExecutor(service.state, "synthetic-interrupted-writer"):
        service.state.claim_operation(identifier, "synthetic-interrupted-writer")
        service.state.mark_outcome_unknown(identifier, "synthetic lost response")
    before = counts(service)
    new_ref = repack(service, ref, new_id=True)
    admit(service, new_ref)
    review = service.plan_review(new_ref, **options)
    assert review["selection"]["blocked_operations"] == [identifier]
    with pytest.raises(PackageAdmissionConflict, match="prerequisites"):
        service.prepare_plan(new_ref, expected_review_digest=review["review_digest"], **options)
    assert service.state.get_operation(identifier).status == "outcome_unknown"
    assert counts(service)["object_origins"] == before["object_origins"]
    assert counts(service)["operations"] == before["operations"]


def test_plan_requires_receipt_and_execution_rechecks_package_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    options = dict(target_instance_key="b", profile_revision=1, include_metadata=False)
    review = service.plan_review(ref, **options)
    with pytest.raises(PackageAdmissionConflict, match="matching local receipt"):
        service.prepare_plan(ref, expected_review_digest=review["review_digest"], **options)
    admit(service, ref)
    new_ref = repack(service, ref, new_id=True)
    with pytest.raises(PackageAdmissionConflict, match="changed after admission"):
        service.verify_receipt(
            new_ref, target_instance_key="b", expected_digest=review["digest_sha256"]
        )
    assert counts(service)["operations"] == 0


def test_schema_eight_migration_retains_registered_installation(tmp_path: Path) -> None:
    database = tmp_path / "version-eight.sqlite3"
    with sqlite3.connect(database) as connection:
        for version in range(1, 9):
            connection.executescript(MIGRATIONS[version])
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)", (version, "synthetic")
            )
        connection.execute("PRAGMA user_version=8")
        connection.execute(
            "INSERT INTO instances VALUES (?,?,?,?,?)",
            (
                "existing",
                "Existing installation",
                "https://existing.invalid",
                "stable-marker",
                "synthetic",
            ),
        )
    state = StateStore(database)
    assert state.get_instance("existing").installation_marker == "stable-marker"
    with state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM package_receipts").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize("action", ["admit", "selection"])
def test_metadata_replacement_after_inspection_cannot_escape_package_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    verified = service.artifact(ref)
    before = counts(service)
    manifest = verified.directory / "metadata/manifest.json"
    document = json.loads(manifest.read_text())
    document["objects"][0]["comparison"]["current"]["title"] = "Unreviewed replacement"
    raw = (json.dumps(document) + "\n").encode()
    manifest.write_bytes(raw)
    manifest.with_name("manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    # Simulate replacement after inspect_package returned but before its consumer
    # opens metadata. The replacement itself has a valid manifest byte digest.
    monkeypatch.setattr(service, "artifact", lambda _: verified)
    with pytest.raises(PackageError, match="changed after package inspection"):
        if action == "admit":
            service.admit(
                ref,
                target_instance_key="b",
                profile_revision=1,
                expected_digest=verified.digest_sha256,
            )
        else:
            build_package_selection(
                service.state,
                verified,
                ref,
                include_metadata=True,
                apply_members=False,
                author_credential_refs={},
            )
    assert counts(service) == before


def test_exact_byte_alias_records_actual_plan_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    admit(service, ref)
    shutil.copytree(service.artifact(ref).directory, service.paths.package_directory / "alias")
    assert admit(service, "alias")["replay"]
    options = dict(target_instance_key="b", profile_revision=1, include_metadata=False)
    review = service.plan_review("alias", **options)
    plan = service.prepare_plan("alias", expected_review_digest=review["review_digest"], **options)
    assert plan["document"]["inventory"]["request"]["package_ref"] == "alias"
    assert service.list()[0]["package_ref"] == ref


def test_unregistered_preserved_structure_origin_is_explicit_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    artifact = service.artifact(ref)
    artifact.document["repositories"][0]["origin"]["instance_key"] = "unknown-origin"
    destination = service.paths.package_directory / "unknown"
    shutil.copytree(artifact.directory, destination)
    (destination / "package.json").unlink()
    (destination / "package.sha256").unlink()
    write_package(destination, artifact.document)
    before = counts(service)
    with pytest.raises(ManifestConflict, match="unregistered instance"):
        admit(service, "unknown")
    assert counts(service) == before
