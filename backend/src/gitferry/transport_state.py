"""Transactional admission of portable facts into one local GitFerry state store."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal, cast

from gitferry.state_codec import canonical_json, now, origin_id
from gitferry.state_store import StateStore
from gitferry.transport import ManifestConflict, TransportArtifact


@dataclass(frozen=True)
class AdmissionReport:
    manifest_id: str
    digest_sha256: str
    status: Literal["recorded", "admitted", "replay", "admitted-with-blocks"]
    origin_ids: tuple[str, ...]
    blocked_operations: tuple[str, ...] = ()


AdmissionStatus = Literal["recorded", "admitted", "replay", "admitted-with-blocks"]


def _instance(connection: sqlite3.Connection, key: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM instances WHERE instance_key = ?", (key,)).fetchone()
    if row is None:
        raise ManifestConflict(f"transport references unregistered instance: {key}")
    return cast(sqlite3.Row, row)


def _check_endpoint(connection: sqlite3.Connection, endpoint: dict[str, Any], *, role: str) -> None:
    registered = _instance(connection, endpoint["instance_key"])
    if registered["installation_marker"] != endpoint["installation_marker"]:
        raise ManifestConflict(f"{role} installation marker does not match local registration")


def _existing_manifest(
    connection: sqlite3.Connection, artifact: TransportArtifact, direction: str
) -> AdmissionReport | None:
    document = artifact.document
    row = connection.execute(
        "SELECT digest_sha256, direction FROM transport_manifests WHERE manifest_id = ?",
        (document["manifest_id"],),
    ).fetchone()
    if row is None:
        return None
    if row["digest_sha256"] != artifact.digest_sha256:
        raise ManifestConflict("manifest id already has another digest")
    if row["direction"] != direction:
        raise ManifestConflict("manifest id is already recorded with another local direction")
    origins = tuple(
        origin_id(
            item["origin"]["instance_key"],
            item["origin"]["repository"],
            _internal_object_type(item["origin"]["object_type"]),
            item["origin"]["global_id"],
        )
        for item in document["objects"]
    )
    return AdmissionReport(document["manifest_id"], artifact.digest_sha256, "replay", origins)


def _forward_row(
    connection: sqlite3.Connection, document: dict[str, Any], expected_direction: str
) -> sqlite3.Row | None:
    reference = document.get("forward_reference")
    if reference is None:
        return None
    row = connection.execute(
        "SELECT * FROM transport_manifests WHERE manifest_id = ?",
        (reference["manifest_id"],),
    ).fetchone()
    if (
        row is None
        or row["direction"] != expected_direction
        or row["mode"] != "forward"
        or row["digest_sha256"] != reference["digest_sha256"]
    ):
        raise ManifestConflict(
            "return manifest references an unknown or mismatched forward transfer"
        )
    return cast(sqlite3.Row, row)


def _internal_object_type(portable_type: str) -> str:
    return "comment" if portable_type == "issue_comment" else portable_type


def _portable_origin(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    value = item["origin"]
    identifier = origin_id(
        value["instance_key"],
        value["repository"],
        _internal_object_type(value["object_type"]),
        value["global_id"],
    )
    return identifier, value


def _parent_origin_id(origin: dict[str, Any]) -> str | None:
    parent = origin.get("parent_issue_origin")
    if parent is None:
        return None
    return origin_id(
        parent["instance_key"],
        parent["repository"],
        "issue",
        parent["global_id"],
    )


def _forward_facts(
    connection: sqlite3.Connection, forward_manifest_id: str
) -> dict[str, dict[str, Any]]:
    return {
        str(row["origin_id"]): json.loads(row["facts_json"])
        for row in connection.execute(
            "SELECT origin_id, facts_json FROM transport_manifest_objects WHERE manifest_id = ?",
            (forward_manifest_id,),
        )
    }


def _check_return_baselines(
    connection: sqlite3.Connection, document: dict[str, Any], forward: sqlite3.Row | None
) -> None:
    if forward is None:
        return
    prior = _forward_facts(connection, forward["manifest_id"])
    sender_key = document["sender"]["instance_key"]
    for item in document["objects"]:
        identifier, origin = _portable_origin(item)
        comparison = item["comparison"]
        previous = prior.get(identifier)
        if previous is None:
            if origin["instance_key"] != sender_key or comparison["original_baseline"] is not None:
                raise ManifestConflict(
                    "return contains an object without a valid new sender origin"
                )
            continue
        if previous["comparison"]["original_baseline"] != comparison["original_baseline"]:
            raise ManifestConflict("return manifest attempts to replace the forward baseline")
        if forward["direction"] == "received":
            observation = connection.execute(
                "SELECT * FROM transport_import_observations "
                "WHERE forward_manifest_id = ? AND origin_id = ?",
                (forward["manifest_id"], identifier),
            ).fetchone()
            if observation is None:
                raise ManifestConflict("return export lacks a confirmed local import observation")
            if json.loads(observation["observed_json"]) != comparison["confirmed_import"]:
                raise ManifestConflict("return confirmed import differs from local observation")


def _check_forward_export_baselines(
    connection: sqlite3.Connection, document: dict[str, Any], direction: str
) -> None:
    if direction != "exported" or document["mode"] != "forward":
        return
    for item in document["objects"]:
        identifier, _ = _portable_origin(item)
        comparison = item["comparison"]
        row = connection.execute(
            "SELECT baseline_json FROM baselines WHERE transfer_round_id = ? AND origin_id = ? "
            "AND ruleset_name = ? AND ruleset_version = ?",
            (
                comparison["baseline_origin"]["transfer_round_id"],
                identifier,
                comparison["ruleset"],
                comparison["ruleset_version"],
            ),
        ).fetchone()
        if row is None:
            raise ManifestConflict("forward export has no matching immutable local baseline")
        if json.loads(row["baseline_json"]) != comparison["original_baseline"]:
            raise ManifestConflict("forward export baseline differs from immutable local baseline")


def _insert_origin(connection: sqlite3.Connection, item: dict[str, Any]) -> str:
    identifier, origin = _portable_origin(item)
    object_type = _internal_object_type(origin["object_type"])
    issue_index = int(origin["issue_index"]) if object_type == "issue" else None
    parent_identifier = _parent_origin_id(origin)
    expected = (
        origin["instance_key"],
        origin["repository"],
        object_type,
        origin["global_id"],
        issue_index,
        parent_identifier,
        origin.get("created_at"),
    )
    existing = connection.execute(
        "SELECT * FROM object_origins WHERE origin_id = ?", (identifier,)
    ).fetchone()
    if existing is not None:
        actual = (
            existing["origin_instance_key"],
            existing["origin_repository_scope"],
            existing["object_type"],
            existing["origin_global_id"],
            existing["origin_issue_index"],
            existing["parent_origin_id"],
            existing["origin_created_at"],
        )
        if actual != expected:
            raise ManifestConflict("origin reference conflicts with the local immutable origin")
        return identifier
    connection.execute(
        "INSERT INTO object_origins VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            identifier,
            origin["instance_key"],
            origin["repository"],
            object_type,
            origin["global_id"],
            issue_index,
            parent_identifier,
            origin.get("created_at"),
            now(),
        ),
    )
    return identifier


def _insert_replica(
    connection: sqlite3.Connection,
    origin_identifier: str,
    object_type: str,
    replica: dict[str, Any],
) -> None:
    issue_index_key = "issue_index" if object_type == "issue" else "parent_issue_index"
    issue_index = int(replica[issue_index_key]) if object_type in ("issue", "comment") else None
    expected = (
        replica["repository"],
        replica["global_id"],
        issue_index,
        replica.get("native_created_at"),
    )
    existing = connection.execute(
        "SELECT * FROM object_mappings WHERE origin_id = ? AND target_instance_key = ?",
        (origin_identifier, replica["instance_key"]),
    ).fetchone()
    if existing is not None:
        actual = (
            existing["target_repository_scope"],
            existing["target_global_id"],
            existing["target_issue_index"],
            existing["target_native_created_at"],
        )
        if actual != expected:
            raise ManifestConflict("replica reference conflicts with an immutable local mapping")
        return
    try:
        connection.execute(
            "INSERT INTO object_mappings "
            "(origin_id, target_instance_key, target_repository_scope, target_object_type, "
            "target_global_id, target_issue_index, target_native_created_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                origin_identifier,
                replica["instance_key"],
                replica["repository"],
                object_type,
                replica["global_id"],
                issue_index,
                replica.get("native_created_at"),
                now(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ManifestConflict("replica reference conflicts with another local mapping") from exc


def _record_in_connection(
    connection: sqlite3.Connection,
    artifact: TransportArtifact,
    *,
    local_instance_key: str,
    direction: Literal["exported", "received"],
) -> AdmissionReport:
    document = artifact.document
    local_role = "sender" if direction == "exported" else "recipient"
    expected_forward_direction = "received" if direction == "exported" else "exported"
    replay = _existing_manifest(connection, artifact, direction)
    if replay is not None:
        return replay
    endpoint = document[local_role]
    if endpoint["instance_key"] != local_instance_key:
        raise ManifestConflict(f"manifest {local_role} does not match this local context")
    _check_endpoint(connection, document["sender"], role="sender")
    _check_endpoint(connection, document["recipient"], role="recipient")
    forward = _forward_row(connection, document, expected_forward_direction)
    _check_return_baselines(connection, document, forward)
    _check_forward_export_baselines(connection, document, direction)

    reference = document.get("forward_reference")
    connection.execute(
        "INSERT INTO transport_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            document["manifest_id"],
            artifact.digest_sha256,
            document["transfer_round_id"],
            direction,
            document["mode"],
            document["sender"]["instance_key"],
            document["recipient"]["instance_key"],
            reference["manifest_id"] if reference else None,
            reference["digest_sha256"] if reference else None,
            canonical_json(document),
            now(),
        ),
    )
    origin_ids: list[str] = []
    blocked: set[str] = set()
    items_by_identifier = {_portable_origin(item)[0]: item for item in document["objects"]}
    # Resolve the complete object graph before recording any portable facts. Comments may
    # appear before parents in the file, but the relational insert is parent-first.
    for item in document["objects"]:
        _, portable_origin = _portable_origin(item)
        parent_identifier = _parent_origin_id(portable_origin)
        if parent_identifier is None:
            continue
        parent_item = items_by_identifier.get(parent_identifier)
        parent_row = connection.execute(
            "SELECT object_type FROM object_origins WHERE origin_id = ?",
            (parent_identifier,),
        ).fetchone()
        if parent_item is None and parent_row is None:
            raise ManifestConflict("comment parent issue origin cannot be resolved")
        if parent_item is not None and parent_item["origin"]["object_type"] != "issue":
            raise ManifestConflict("comment parent reference is not an issue origin")
        if parent_row is not None and parent_row["object_type"] != "issue":
            raise ManifestConflict("comment parent reference conflicts with local origin type")
    ordered = sorted(
        document["objects"],
        key=lambda item: _internal_object_type(item["origin"]["object_type"]) == "comment",
    )
    for item in ordered:
        _insert_origin(connection, item)
    for item in document["objects"]:
        identifier, portable_origin = _portable_origin(item)
        origin_ids.append(identifier)
        object_type = _internal_object_type(portable_origin["object_type"])
        for replica in item["replicas"]:
            _instance(connection, replica["instance_key"])
            _insert_replica(connection, identifier, object_type, replica)
        connection.execute(
            "INSERT INTO transport_manifest_objects VALUES (?, ?, ?)",
            (document["manifest_id"], identifier, canonical_json(item)),
        )
        blocked.update(
            str(row[0])
            for row in connection.execute(
                "SELECT operation_id FROM operations WHERE origin_id = ? "
                "AND target_instance_key = ? "
                "AND status IN ('blocked_parent', 'outcome_unknown', 'conflict')",
                (identifier, local_instance_key),
            )
        )
    for index, claim in enumerate(document["identity_claims"]):
        relationship = claim["relationship"]
        _instance(connection, relationship["instance_key"])
        connection.execute(
            "INSERT INTO transport_identity_claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document["manifest_id"],
                index,
                claim["realm_key"],
                claim["login"],
                claim["external_id"],
                claim["ou_path"],
                relationship["instance_key"],
                relationship["account_id"],
                relationship["login"],
                relationship["status"],
            ),
        )
    status: AdmissionStatus
    if direction == "exported":
        status = "recorded"
    elif blocked:
        status = "admitted-with-blocks"
    else:
        status = "admitted"
    return AdmissionReport(
        document["manifest_id"],
        artifact.digest_sha256,
        status,
        tuple(origin_ids),
        tuple(sorted(blocked)),
    )


def _record(
    state: StateStore,
    artifact: TransportArtifact,
    *,
    local_instance_key: str,
    direction: Literal["exported", "received"],
) -> AdmissionReport:
    with state.transaction() as connection:
        return _record_in_connection(
            connection, artifact, local_instance_key=local_instance_key, direction=direction
        )


def record_export(
    state: StateStore, artifact: TransportArtifact, *, local_instance_key: str
) -> AdmissionReport:
    return _record(state, artifact, local_instance_key=local_instance_key, direction="exported")


def admit_transport(
    state: StateStore, artifact: TransportArtifact, *, local_instance_key: str
) -> AdmissionReport:
    return _record(state, artifact, local_instance_key=local_instance_key, direction="received")


def confirm_import_observation(
    state: StateStore,
    *,
    forward_manifest_id: str,
    origin_identifier: str,
    observed: dict[str, Any],
    target_native_created_at: str | None,
) -> None:
    """Freeze the selected B state observed immediately after a confirmed import."""

    encoded = canonical_json(observed)
    with state.transaction() as connection:
        manifest = connection.execute(
            "SELECT * FROM transport_manifests WHERE manifest_id = ?",
            (forward_manifest_id,),
        ).fetchone()
        if manifest is None or manifest["direction"] != "received" or manifest["mode"] != "forward":
            raise ManifestConflict("import observation requires a received forward manifest")
        item = connection.execute(
            "SELECT facts_json FROM transport_manifest_objects "
            "WHERE manifest_id = ? AND origin_id = ?",
            (forward_manifest_id, origin_identifier),
        ).fetchone()
        if item is None:
            raise ManifestConflict("import observation origin is not in the forward manifest")
        facts = json.loads(item["facts_json"])
        expected = (
            facts["comparison"]["ruleset"],
            facts["comparison"]["ruleset_version"],
            encoded,
            target_native_created_at,
        )
        existing = connection.execute(
            "SELECT * FROM transport_import_observations "
            "WHERE forward_manifest_id = ? AND origin_id = ?",
            (forward_manifest_id, origin_identifier),
        ).fetchone()
        if existing is not None:
            actual = (
                existing["ruleset_name"],
                existing["ruleset_version"],
                existing["observed_json"],
                existing["target_native_created_at"],
            )
            if actual != expected:
                raise ManifestConflict("confirmed import observation is immutable")
            return
        connection.execute(
            "INSERT INTO transport_import_observations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                forward_manifest_id,
                origin_identifier,
                expected[0],
                expected[1],
                encoded,
                target_native_created_at,
                now(),
            ),
        )


def get_import_observation(
    state: StateStore, *, forward_manifest_id: str, origin_identifier: str
) -> dict[str, Any]:
    with state.transaction() as connection:
        row = connection.execute(
            "SELECT observed_json FROM transport_import_observations "
            "WHERE forward_manifest_id = ? AND origin_id = ?",
            (forward_manifest_id, origin_identifier),
        ).fetchone()
        if row is None:
            raise KeyError("confirmed import observation not found")
        return cast(dict[str, Any], json.loads(row["observed_json"]))
