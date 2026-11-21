"""Atomic local recording of read-only source captures using existing transport rules."""

from __future__ import annotations

import hashlib
import json

from gitferry.application_package import PackageArtifact, PackageError
from gitferry.state_codec import canonical_json, now, origin_id
from gitferry.state_store import StateStore
from gitferry.transport import load_transport
from gitferry.transport_state import (
    _check_endpoint,
    _insert_origin,
    _insert_replica,
    _record_in_connection,
)


def record_source_capture(state: StateStore, artifact: PackageArtifact, export_id: str) -> None:
    """Freeze origins, source replicas, baselines, manifest and capture status together."""
    document = artifact.document
    metadata = load_transport(artifact.directory / "metadata") if document["metadata"] else None
    sender = document["sender"]["instance_key"]
    recipient = document["recipient"]["instance_key"]
    with state.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM source_exports WHERE export_id=?", (export_id,)
        ).fetchone()
        if row is None:
            raise PackageError("source capture has no local intent")
        if row["status"] == "captured":
            if row["package_digest"] != artifact.digest_sha256:
                raise PackageError("confirmed source package bytes changed")
            return
        if row["candidate_digest"] != artifact.digest_sha256:
            raise PackageError("package does not match the locally captured byte digest")
        _check_endpoint(connection, document["sender"], role="sender")
        _check_endpoint(connection, document["recipient"], role="recipient")
        request = json.loads(row["request_json"])
        if (
            document["package_id"] != export_id
            or document["transfer_round_id"] != export_id
            or sender != request["source_instance_key"]
            or recipient != request["recipient_instance_key"]
            or sorted(item["repository"] for item in document["repositories"])
            != sorted(item["repository"] for item in request["repositories"])
            or (metadata is not None) != request["include_metadata"]
        ):
            raise PackageError("captured package disagrees with local export intent")
        if request["version"] == 2 and (
            document["schema_version"] != 2
            or document["inventory"]["snapshot_id"] != request["inventory_id"]
            or document["inventory"]["content_digest"] != request["inventory_digest"]
            or document["inventory"]["include_members"] != request["include_members"]
        ):
            raise PackageError("captured inventory disagrees with local export intent")
        # Structure origins use the same immutable origin comparison as metadata admission.
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
                identifier = _insert_origin(
                    connection, {"origin": {**identity, "repository": scope, "object_type": kind}}
                )
                if identity["instance_key"] == sender:
                    _insert_replica(
                        connection,
                        identifier,
                        kind,
                        {
                            "instance_key": sender,
                            "repository": scope,
                            "global_id": identity["global_id"],
                        },
                    )
                else:
                    known_copy = connection.execute(
                        "SELECT target_repository_scope FROM object_mappings "
                        "WHERE origin_id=? AND target_instance_key=?",
                        (identifier, sender),
                    ).fetchone()
                    if known_copy is None or known_copy[0] != scope:
                        raise PackageError("captured structure lacks its known source replica")
        if metadata is not None:
            items = metadata.document["objects"]
            for item in sorted(
                items, key=lambda value: value["origin"]["object_type"] == "issue_comment"
            ):
                _insert_origin(connection, item)
            selection = canonical_json(
                {"repositories": sorted(item["repository"] for item in document["repositories"])}
            )
            existing = connection.execute(
                "SELECT * FROM transfer_rounds WHERE transfer_round_id=?", (export_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO transfer_rounds VALUES (?,?,?,?,?)",
                    (export_id, sender, recipient, selection, now()),
                )
            elif (
                existing["origin_instance_key"],
                existing["target_instance_key"],
                existing["selection_key"],
            ) != (sender, recipient, selection):
                raise PackageError("source capture round conflicts with its original selection")
            for item in items:
                origin = item["origin"]
                kind = "comment" if origin["object_type"] == "issue_comment" else "issue"
                identifier = origin_id(
                    origin["instance_key"], origin["repository"], kind, origin["global_id"]
                )
                comparison = item["comparison"]
                current = canonical_json(comparison["current"])
                if comparison["original_baseline"] != comparison["current"]:
                    raise PackageError("source capture baseline is not its selected observation")
                source_replica = next(
                    (copy for copy in item["replicas"] if copy["instance_key"] == sender), None
                )
                if source_replica is None:
                    raise PackageError("source capture has no observed source replica")
                for replica in item["replicas"]:
                    _insert_replica(connection, identifier, kind, replica)
                baseline_id = (
                    "capture-" + hashlib.sha256((export_id + identifier).encode()).hexdigest()
                )
                old = connection.execute(
                    "SELECT baseline_json FROM baselines WHERE baseline_id=?", (baseline_id,)
                ).fetchone()
                if old is not None:
                    if old["baseline_json"] != current:
                        raise PackageError("source capture cannot replace an immutable baseline")
                else:
                    timestamp = now()
                    connection.execute(
                        "INSERT INTO baselines VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?)",
                        (
                            baseline_id,
                            export_id,
                            identifier,
                            comparison["ruleset"],
                            comparison["ruleset_version"],
                            current,
                            current,
                            current,
                            origin.get("created_at"),
                            source_replica.get("native_created_at"),
                            timestamp,
                            timestamp,
                        ),
                    )
            _record_in_connection(
                connection, metadata, local_instance_key=sender, direction="exported"
            )
        connection.execute(
            "UPDATE source_exports SET status='captured', package_digest=?, "
            "error_code=NULL, updated_at=? WHERE export_id=?",
            (artifact.digest_sha256, now(), export_id),
        )
