"""Local intake of validated package facts; imported files never grant authority."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from gitferry.application_package import (
    PackageArtifact,
    PackageError,
    inspect_package,
    load_package_metadata,
)
from gitferry.connection_profiles import _identifier
from gitferry.runtime import RuntimePaths
from gitferry.state_codec import canonical_json, now
from gitferry.state_store import StateStore
from gitferry.transfer_plans import PlanService
from gitferry.transport import TransportArtifact
from gitferry.transport_state import (
    _check_endpoint,
    _insert_origin,
    _insert_replica,
    _instance,
    _portable_origin,
    _record_in_connection,
)


class PackageAdmissionConflict(PackageError):
    """Received facts disagree with immutable bytes or local authority."""


def _authority(connection: sqlite3.Connection, target: str, revision: int) -> str:
    row = connection.execute(
        "SELECT approved_identity FROM connection_checks WHERE instance_key=?", (target,)
    ).fetchone()
    if row is None or not row[0]:
        raise PackageAdmissionConflict("target connection needs local approval")
    digest = hashlib.sha256(row[0].encode()).hexdigest()
    PlanService._profile(connection, target, revision, digest)
    return digest


def _local_replicas(
    connection: sqlite3.Connection, artifact: TransportArtifact, target: str
) -> None:
    # Portable observations may describe remote copies. A claim about this local
    # target cannot establish a new trusted target mapping without reconciliation.
    for item in artifact.document["objects"]:
        _instance(connection, item["origin"]["instance_key"])
        identifier, _ = _portable_origin(item)
        if (
            item["origin"]["instance_key"] == target
            and connection.execute(
                "SELECT 1 FROM object_origins WHERE origin_id=?", (identifier,)
            ).fetchone()
            is None
        ):
            raise PackageAdmissionConflict("package proposes an unverified local object origin")
        for replica in item["replicas"]:
            if (
                replica["instance_key"] == target
                and connection.execute(
                    "SELECT 1 FROM object_mappings WHERE origin_id=? AND target_instance_key=?",
                    (identifier, target),
                ).fetchone()
                is None
            ):
                raise PackageAdmissionConflict(
                    "package proposes an unverified local target replica"
                )


def _facts(
    connection: sqlite3.Connection, artifact: PackageArtifact, package_ref: str, target: str
) -> dict[str, Any]:
    document = artifact.document
    if document["recipient"]["instance_key"] != target:
        raise PackageAdmissionConflict("package recipient is not the locally selected target")
    _check_endpoint(connection, document["sender"], role="sender")
    _check_endpoint(connection, document["recipient"], role="recipient")
    previous = connection.execute(
        "SELECT * FROM package_receipts WHERE package_id=?", (document["package_id"],)
    ).fetchone()
    if previous is not None and (
        previous["digest_sha256"] != artifact.digest_sha256
        or previous["target_instance_key"] != target
    ):
        raise PackageAdmissionConflict("package ID already identifies different received bytes")
    reference = connection.execute(
        "SELECT package_id FROM package_receipts WHERE package_ref=?", (package_ref,)
    ).fetchone()
    if reference is not None and reference[0] != document["package_id"]:
        raise PackageAdmissionConflict("local package reference already belongs to another package")

    origins: set[str] = set()
    for repository in document["repositories"]:
        organization = repository["organization"]
        structures = [
            ("organization", organization["name"], organization["origin"]),
            ("repository", repository["repository"], repository["origin"]),
        ]
        structures.extend(
            ("team", organization["name"], team["origin"]) for team in repository["teams"]
        )
        for kind, scope, identity in structures:
            _instance(connection, identity["instance_key"])
            portable = {**identity, "repository": scope, "object_type": kind}
            identifier, _ = _portable_origin({"origin": portable})
            prior_scopes = connection.execute(
                "SELECT origin_repository_scope FROM object_origins "
                "WHERE origin_instance_key=? AND object_type=? AND origin_global_id=?",
                (identity["instance_key"], kind, identity["global_id"]),
            ).fetchall()
            if any(row[0] != scope for row in prior_scopes):
                raise PackageAdmissionConflict(
                    "package structure scope disagrees with its known permanent origin"
                )
            if (
                identity["instance_key"] == target
                and connection.execute(
                    "SELECT 1 FROM object_origins WHERE origin_id=?", (identifier,)
                ).fetchone()
                is None
            ):
                raise PackageAdmissionConflict(
                    "package proposes an unverified local structure origin"
                )
            identifier = _insert_origin(connection, {"origin": portable})
            origins.add(identifier)
            # This is the origin-side copy, not a newly approved local target copy.
            if identity["instance_key"] != target:
                _insert_replica(
                    connection,
                    identifier,
                    kind,
                    {
                        "instance_key": identity["instance_key"],
                        "repository": scope,
                        "global_id": identity["global_id"],
                    },
                )
    if document["metadata"] is not None:
        metadata = load_package_metadata(artifact)
        _local_replicas(connection, metadata, target)
        admitted = _record_in_connection(
            connection, metadata, local_instance_key=target, direction="received"
        )
        origins.update(admitted.origin_ids)
    blocked: list[dict[str, Any]] = []
    for identifier in sorted(origins):
        blocked.extend(
            dict(row)
            for row in connection.execute(
                "SELECT operation_id,status FROM operations WHERE origin_id=? "
                "AND target_instance_key=? "
                "AND status IN "
                "('outcome_unknown','executing','conflict','failed','blocked_parent')",
                (identifier, target),
            )
        )
    if previous is None:
        connection.execute(
            "INSERT INTO package_receipts VALUES (?,?,?,?,?,?)",
            (
                document["package_id"],
                artifact.digest_sha256,
                target,
                package_ref,
                canonical_json(document),
                now(),
            ),
        )
    return {
        "package_id": document["package_id"],
        "digest_sha256": artifact.digest_sha256,
        "target_instance_key": target,
        "origin_ids": sorted(origins),
        "blocked_operations": blocked,
        "replay": previous is not None,
        "inventory_complete": False,
        "limitations": document["limitations"],
        "realms": document["realms"],
        "execution_authorized": False,
    }


class PackageAdmissions:
    def __init__(self, state: StateStore, paths: RuntimePaths):
        self.state, self.paths = state, paths

    def artifact(self, package_ref: str) -> PackageArtifact:
        _identifier(package_ref)
        root = self.paths.package_directory
        if root.is_symlink() or not root.is_dir():
            raise PackageAdmissionConflict("package directory is not an owned local directory")
        return inspect_package(root / package_ref)

    def review(
        self, package_ref: str, *, target_instance_key: str, profile_revision: int
    ) -> dict[str, Any]:
        artifact = self.artifact(package_ref)
        with self.state.transaction() as connection:
            authority = _authority(connection, target_instance_key, profile_revision)
            connection.execute("SAVEPOINT package_review")
            try:
                report = _facts(connection, artifact, package_ref, target_instance_key)
            finally:
                connection.execute("ROLLBACK TO package_review")
                connection.execute("RELEASE package_review")
        report.update(
            status="reviewed", profile_revision=profile_revision, authority_digest=authority
        )
        return report

    def admit(
        self,
        package_ref: str,
        *,
        target_instance_key: str,
        profile_revision: int,
        expected_digest: str,
    ) -> dict[str, Any]:
        artifact = self.artifact(package_ref)
        if artifact.digest_sha256 != expected_digest:
            raise PackageAdmissionConflict("package bytes changed since local review")
        with self.state.transaction() as connection:
            _authority(connection, target_instance_key, profile_revision)
            report = _facts(connection, artifact, package_ref, target_instance_key)
        report["status"] = "admitted-facts-only"
        return report

    def verify_receipt(
        self, package_ref: str, *, target_instance_key: str, expected_digest: str
    ) -> PackageArtifact:
        artifact = self.artifact(package_ref)
        if artifact.digest_sha256 != expected_digest:
            raise PackageAdmissionConflict("package bytes changed after admission")
        with self.state.read_connection() as connection:
            receipt = connection.execute(
                "SELECT * FROM package_receipts WHERE package_id=?",
                (artifact.document["package_id"],),
            ).fetchone()
        if receipt is None or (
            receipt["digest_sha256"] != expected_digest
            or receipt["target_instance_key"] != target_instance_key
            or artifact.document["recipient"]["instance_key"] != target_instance_key
        ):
            raise PackageAdmissionConflict("package has no matching local receipt")
        return artifact

    def list(self) -> list[dict[str, Any]]:
        with self.state.read_connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT package_id,digest_sha256,target_instance_key,package_ref,received_at "
                    "FROM package_receipts ORDER BY received_at DESC LIMIT 100"
                )
            ]

    def plan_review(
        self,
        package_ref: str,
        *,
        target_instance_key: str,
        profile_revision: int,
        include_metadata: bool = True,
        apply_members: bool = False,
        author_credential_refs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from gitferry.package_selection import build_package_selection

        report = self.review(
            package_ref, target_instance_key=target_instance_key, profile_revision=profile_revision
        )
        artifact = self.artifact(package_ref)
        if artifact.digest_sha256 != report["digest_sha256"]:
            raise PackageAdmissionConflict("package changed during selection review")
        selection = build_package_selection(
            self.state,
            artifact,
            package_ref,
            include_metadata=include_metadata,
            apply_members=apply_members,
            author_credential_refs=author_credential_refs or {},
        )
        report["selection"] = selection
        review_facts = {
            key: report[key]
            for key in (
                "digest_sha256",
                "target_instance_key",
                "profile_revision",
                "authority_digest",
                "selection",
            )
        }
        report["review_digest"] = hashlib.sha256(canonical_json(review_facts).encode()).hexdigest()
        return report

    def prepare_plan(
        self,
        package_ref: str,
        *,
        target_instance_key: str,
        profile_revision: int,
        expected_review_digest: str,
        include_metadata: bool = True,
        apply_members: bool = False,
        author_credential_refs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from gitferry.plan_application import prepare_transfer_plan

        review = self.plan_review(
            package_ref,
            target_instance_key=target_instance_key,
            profile_revision=profile_revision,
            include_metadata=include_metadata,
            apply_members=apply_members,
            author_credential_refs=author_credential_refs,
        )
        if review["review_digest"] != expected_review_digest:
            raise PackageAdmissionConflict("local package selection changed; review it again")
        selection = review["selection"]
        if any(
            selection[key]
            for key in (
                "missing_bindings",
                "missing_credentials",
                "waiting_parents",
                "blocked_operations",
            )
        ):
            raise PackageAdmissionConflict("local package prerequisites remain unresolved")
        self.verify_receipt(
            package_ref,
            target_instance_key=target_instance_key,
            expected_digest=review["digest_sha256"],
        )
        return prepare_transfer_plan(
            self.state, selection["request"], expected_profile_revision=profile_revision
        )
