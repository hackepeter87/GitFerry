"""Durable, fail-closed SQLite state for bounded GitFerry operations."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from gitferry.state_codec import (
    canonical_json as _canonical_json,
)
from gitferry.state_codec import (
    decode_object as _decode_object,
)
from gitferry.state_codec import (
    find_secret_field as _contains_secret,
)
from gitferry.state_codec import (
    now as _now,
)
from gitferry.state_codec import origin_id as _origin_id
from gitferry.state_codec import (
    require_text as _require_text,
)
from gitferry.state_migrations import MIGRATIONS, SCHEMA_VERSION
from gitferry.state_models import (
    BaselineRecord,
    BindingConflict,
    ExecutorLeaseConflict,
    IdentityBindingRecord,
    InstanceRegistration,
    MappingConflict,
    ObjectMappingRecord,
    ObjectType,
    OperationBlocked,
    OperationConflict,
    OperationRecord,
    OperationStatus,
    OriginKey,
    OriginRecord,
    RealmRegistration,
    StateStoreError,
    TargetConfirmation,
    UnsupportedSchemaVersion,
)

__all__ = [
    "BaselineRecord",
    "BindingConflict",
    "ExecutorLeaseConflict",
    "IdentityBindingRecord",
    "InstanceRegistration",
    "MappingConflict",
    "ObjectMappingRecord",
    "OperationBlocked",
    "OperationConflict",
    "OperationRecord",
    "OriginKey",
    "OriginRecord",
    "RealmRegistration",
    "StateStore",
    "StateStoreError",
    "TargetConfirmation",
    "UnsupportedSchemaVersion",
]

_MAPPING_PRESERVING_OPERATION_TYPES = frozenset(
    {
        "update_issue",
        "update_comment",
        "assign_team_repository",
        "add_team_member",
        "push_git_bundle",
    }
)

SchemaSignature = tuple[
    tuple[
        str,
        tuple[tuple[Any, ...], ...],
        tuple[tuple[Any, ...], ...],
        tuple[tuple[str, int, str, int, tuple[tuple[Any, ...], ...]], ...],
    ],
    ...,
]


def _apply_migrations(connection: sqlite3.Connection, current: int) -> None:
    for version in range(current + 1, SCHEMA_VERSION + 1):
        if version in (3, 4):
            # Rebuilding a referenced table is the documented SQLite migration
            # pattern. Foreign keys must be disabled outside the transaction and
            # are checked in full before the connection is accepted again.
            connection.execute("PRAGMA foreign_keys = OFF")
        applied_at = _now().replace("'", "''")
        script = (
            "BEGIN IMMEDIATE;\n"
            + MIGRATIONS[version]
            + f"\nINSERT INTO schema_migrations VALUES ({version}, '{applied_at}');\n"
            + f"PRAGMA user_version = {version};\nCOMMIT;"
        )
        connection.executescript(script)
        if version in (3, 4):
            connection.execute("PRAGMA foreign_keys = ON")
            violations = list(connection.execute("PRAGMA foreign_key_check"))
            if violations:
                raise StateStoreError(f"schema migration {version} left foreign-key violations")


def _schema_signature(connection: sqlite3.Connection) -> SchemaSignature:
    tables = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    signature: list[
        tuple[
            str,
            tuple[tuple[Any, ...], ...],
            tuple[tuple[Any, ...], ...],
            tuple[tuple[str, int, str, int, tuple[tuple[Any, ...], ...]], ...],
        ]
    ] = []
    for table in tables:
        quoted_table = '"' + table.replace('"', '""') + '"'
        columns = tuple(
            tuple(row) for row in connection.execute(f"PRAGMA table_xinfo({quoted_table})")
        )
        foreign_keys = tuple(
            tuple(row) for row in connection.execute(f"PRAGMA foreign_key_list({quoted_table})")
        )
        indexes = []
        for index_row in connection.execute(f"PRAGMA index_list({quoted_table})"):
            index_name = str(index_row[1])
            quoted_index = '"' + index_name.replace('"', '""') + '"'
            index_columns = tuple(
                tuple(row) for row in connection.execute(f"PRAGMA index_xinfo({quoted_index})")
            )
            indexes.append(
                (
                    index_name,
                    int(index_row[2]),
                    str(index_row[3]),
                    int(index_row[4]),
                    index_columns,
                )
            )
        signature.append((table, columns, foreign_keys, tuple(indexes)))
    return tuple(signature)


@lru_cache(maxsize=1)
def _expected_schema_signature() -> SchemaSignature:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        _apply_migrations(connection, 0)
        return _schema_signature(connection)
    finally:
        connection.close()


class StateStore:
    """Short-transaction store; each method configures and closes its connection."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if self.path.exists() and self.path.is_dir():
            raise StateStoreError(f"state database path is a directory: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise StateStoreError("SQLite foreign-key enforcement could not be enabled")
            mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            if mode != "wal":
                raise StateStoreError(f"SQLite WAL mode could not be enabled: {mode}")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except sqlite3.DatabaseError as exc:
            raise StateStoreError(f"cannot open state database {self.path}: {exc}") from exc

    def _migrate(self) -> None:
        connection = self._connect()
        try:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise UnsupportedSchemaVersion(
                    f"database uses newer schema version {current}; supported version is "
                    f"{SCHEMA_VERSION}"
                )
            if current == 0:
                existing = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if existing:
                    raise StateStoreError(
                        "database has tables but no recognized schema version; "
                        "refusing to replace it"
                    )
            _apply_migrations(connection, current)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def connection_settings(self) -> dict[str, int | str]:
        connection = self._connect()
        try:
            return {
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "journal_mode": str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower(),
                "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
            }
        finally:
            connection.close()

    def validate_integrity(self) -> None:
        """Fail unless persisted bytes and schema match the supported state contract."""

        connection = self._connect()
        try:
            integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise StateStoreError("state database integrity validation failed")

            migration_versions = tuple(
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
            if _schema_signature(connection) != _expected_schema_signature() or (
                migration_versions != tuple(range(1, SCHEMA_VERSION + 1))
            ):
                raise StateStoreError(
                    f"state database schema does not match version {SCHEMA_VERSION}"
                )

            if list(connection.execute("PRAGMA foreign_key_check")):
                raise StateStoreError("state database foreign-key validation failed")
        except sqlite3.DatabaseError as exc:
            raise StateStoreError("state database integrity validation failed") from exc
        finally:
            connection.close()

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        """Open one configured read connection without taking the writer lease."""

        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open one short local transaction with the store's durability settings."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def register_instance(
        self,
        instance_key: str,
        display_name: str,
        base_url: str,
        installation_marker: str,
    ) -> InstanceRegistration:
        for field, value in (
            ("instance_key", instance_key),
            ("display_name", display_name),
            ("base_url", base_url),
            ("installation_marker", installation_marker),
        ):
            _require_text(value, field)
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
                (instance_key, display_name, base_url, installation_marker, _now()),
            )
        except sqlite3.IntegrityError:
            existing = self.get_instance(instance_key)
            if existing != InstanceRegistration(
                instance_key, display_name, base_url, installation_marker
            ):
                raise StateStoreError("instance registration must not be redirected") from None
        finally:
            connection.close()
        return self.get_instance(instance_key)

    def get_instance(self, instance_key: str) -> InstanceRegistration:
        row = self._one("SELECT * FROM instances WHERE instance_key = ?", (instance_key,))
        return InstanceRegistration(
            row["instance_key"], row["display_name"], row["base_url"], row["installation_marker"]
        )

    def register_realm(
        self, realm_key: str, display_name: str, external_marker: str
    ) -> RealmRegistration:
        for field, value in (
            ("realm_key", realm_key),
            ("display_name", display_name),
            ("external_marker", external_marker),
        ):
            _require_text(value, field)
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO realms VALUES (?, ?, ?, ?)",
                (realm_key, display_name, external_marker, _now()),
            )
        except sqlite3.IntegrityError:
            existing = self.get_realm(realm_key)
            if existing != RealmRegistration(realm_key, display_name, external_marker):
                raise StateStoreError("realm registration must not be redirected") from None
        finally:
            connection.close()
        return self.get_realm(realm_key)

    def get_realm(self, realm_key: str) -> RealmRegistration:
        row = self._one("SELECT * FROM realms WHERE realm_key = ?", (realm_key,))
        return RealmRegistration(row["realm_key"], row["display_name"], row["external_marker"])

    def confirm_identity_binding(
        self,
        *,
        binding_id: str,
        source_realm_key: str,
        source_login: str,
        source_external_id: str,
        source_ou_path: str,
        target_instance_key: str,
        target_account_id: int,
        target_login: str,
        mapping_version: int,
    ) -> IdentityBindingRecord:
        expected = IdentityBindingRecord(
            binding_id,
            source_realm_key,
            source_login,
            source_external_id,
            source_ou_path,
            target_instance_key,
            target_account_id,
            target_login,
            mapping_version,
        )
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO identity_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    binding_id,
                    source_realm_key,
                    source_login,
                    source_external_id,
                    source_ou_path,
                    target_instance_key,
                    target_account_id,
                    target_login,
                    mapping_version,
                    _now(),
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM identity_bindings WHERE binding_id = ?", (expected.binding_id,)
            ).fetchone()
            if row is not None:
                existing = self._binding(row)
                if existing == expected:
                    return existing
                raise BindingConflict("existing identity binding must not be redirected") from None
            target = connection.execute(
                "SELECT binding_id FROM identity_bindings "
                "WHERE target_instance_key = ? AND target_account_id = ?",
                (expected.target_instance_key, expected.target_account_id),
            ).fetchone()
            if target is not None:
                raise BindingConflict("target account is already bound") from None
            raise BindingConflict("external identity is already bound for this target") from None
        finally:
            connection.close()
        return self.get_identity_binding(expected.binding_id)

    def get_identity_binding(self, binding_id: str) -> IdentityBindingRecord:
        return self._binding(
            self._one("SELECT * FROM identity_bindings WHERE binding_id = ?", (binding_id,))
        )

    def find_identity_binding(
        self,
        *,
        source_realm_key: str,
        source_login: str,
        source_external_id: str,
        source_ou_path: str,
        target_instance_key: str,
    ) -> IdentityBindingRecord:
        """Resolve only an exact, locally confirmed qualified identity binding."""

        return self._binding(
            self._one(
                "SELECT * FROM identity_bindings WHERE source_realm_key = ? "
                "AND source_login = ? AND source_external_id = ? AND source_ou_path = ? "
                "AND target_instance_key = ?",
                (
                    source_realm_key,
                    source_login,
                    source_external_id,
                    source_ou_path,
                    target_instance_key,
                ),
            )
        )

    @staticmethod
    def _binding(row: sqlite3.Row) -> IdentityBindingRecord:
        return IdentityBindingRecord(
            row["binding_id"],
            row["source_realm_key"],
            row["source_login"],
            row["source_external_id"],
            row["source_ou_path"],
            row["target_instance_key"],
            row["target_account_id"],
            row["target_login"],
            row["mapping_version"],
        )

    def register_origin(self, key: OriginKey) -> OriginRecord:
        if key.object_type == "issue" and key.issue_index is None:
            raise ValueError("issue origins require their repository issue index")
        origin_id = _origin_id(
            key.origin_instance_key,
            key.origin_repository_scope,
            key.object_type,
            key.origin_global_id,
        )
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO object_origins VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    origin_id,
                    key.origin_instance_key,
                    key.origin_repository_scope,
                    key.object_type,
                    key.origin_global_id,
                    key.issue_index,
                    key.parent_origin_id,
                    key.origin_created_at,
                    _now(),
                ),
            )
        except sqlite3.IntegrityError:
            existing = self.get_origin(origin_id)
            if existing.key != key:
                raise MappingConflict("origin registration must not be redirected") from None
        finally:
            connection.close()
        return self.get_origin(origin_id)

    def get_origin(self, origin_id: str) -> OriginRecord:
        row = self._one("SELECT * FROM object_origins WHERE origin_id = ?", (origin_id,))
        return OriginRecord(
            row["origin_id"],
            OriginKey(
                row["origin_instance_key"],
                row["origin_repository_scope"],
                cast(ObjectType, row["object_type"]),
                row["origin_global_id"],
                row["origin_issue_index"],
                row["parent_origin_id"],
                row["origin_created_at"],
            ),
        )

    def add_object_mapping(
        self,
        origin_id: str,
        target_instance_key: str,
        target_repository_scope: str,
        target_global_id: str,
        *,
        target_issue_index: int | None = None,
        target_native_created_at: str | None = None,
    ) -> ObjectMappingRecord:
        origin = self.get_origin(origin_id)
        expected = ObjectMappingRecord(
            origin_id,
            target_instance_key,
            target_repository_scope,
            origin.key.object_type,
            target_global_id,
            target_issue_index,
            target_native_created_at,
        )
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO object_mappings "
                "(origin_id, target_instance_key, target_repository_scope, target_object_type, "
                "target_global_id, target_issue_index, target_native_created_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    origin_id,
                    target_instance_key,
                    target_repository_scope,
                    origin.key.object_type,
                    target_global_id,
                    target_issue_index,
                    target_native_created_at,
                    _now(),
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM object_mappings WHERE origin_id = ? AND target_instance_key = ?",
                (origin_id, target_instance_key),
            ).fetchone()
            if row is not None and self._mapping(row) == expected:
                return expected
            raise MappingConflict("existing object mapping must not be redirected") from None
        finally:
            connection.close()
        return self.get_object_mapping(origin_id, target_instance_key)

    def get_object_mapping(self, origin_id: str, target_instance_key: str) -> ObjectMappingRecord:
        return self._mapping(
            self._one(
                "SELECT * FROM object_mappings WHERE origin_id = ? AND target_instance_key = ?",
                (origin_id, target_instance_key),
            )
        )

    @staticmethod
    def _mapping(row: sqlite3.Row) -> ObjectMappingRecord:
        return ObjectMappingRecord(
            row["origin_id"],
            row["target_instance_key"],
            row["target_repository_scope"],
            cast(ObjectType, row["target_object_type"]),
            row["target_global_id"],
            row["target_issue_index"],
            row["target_native_created_at"],
        )

    def create_transfer_round(
        self,
        transfer_round_id: str,
        origin_instance_key: str,
        target_instance_key: str,
        selection_key: str,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO transfer_rounds VALUES (?, ?, ?, ?, ?)",
                (
                    transfer_round_id,
                    origin_instance_key,
                    target_instance_key,
                    selection_key,
                    _now(),
                ),
            )
        finally:
            connection.close()

    def create_baseline(
        self,
        *,
        baseline_id: str,
        transfer_round_id: str,
        origin_id: str,
        ruleset_name: str,
        ruleset_version: int,
        baseline: Mapping[str, Any],
        desired: Mapping[str, Any],
        observed: Mapping[str, Any],
        origin_created_at: str | None,
        target_native_created_at: str | None,
    ) -> BaselineRecord:
        now = _now()
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO baselines VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    baseline_id,
                    transfer_round_id,
                    origin_id,
                    ruleset_name,
                    ruleset_version,
                    _canonical_json(baseline),
                    _canonical_json(desired),
                    _canonical_json(observed),
                    origin_created_at,
                    target_native_created_at,
                    now,
                    now,
                ),
            )
        finally:
            connection.close()
        return self.get_baseline(baseline_id)

    def update_baseline_observation(
        self, baseline_id: str, observed: Mapping[str, Any]
    ) -> BaselineRecord:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE baselines SET observed_json = ?, "
                "observation_revision = observation_revision + 1, updated_at = ? "
                "WHERE baseline_id = ?",
                (_canonical_json(observed), _now(), baseline_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown baseline: {baseline_id}")
        finally:
            connection.close()
        return self.get_baseline(baseline_id)

    def get_baseline(self, baseline_id: str) -> BaselineRecord:
        row = self._one("SELECT * FROM baselines WHERE baseline_id = ?", (baseline_id,))
        return BaselineRecord(
            row["baseline_id"],
            row["transfer_round_id"],
            row["origin_id"],
            row["ruleset_name"],
            row["ruleset_version"],
            _decode_object(row["baseline_json"]),
            _decode_object(row["desired_json"]),
            _decode_object(row["observed_json"]),
            row["observation_revision"],
            row["origin_created_at"],
            row["target_native_created_at"],
        )

    def prepare_operation(
        self,
        *,
        operation_id: str,
        origin_id: str,
        target_instance_key: str,
        target_repository_scope: str,
        operation_type: str,
        mapping_version: int,
        baseline_id: str | None,
        intent: Mapping[str, Any],
    ) -> OperationRecord:
        secret_path = _contains_secret(intent)
        if secret_path:
            raise ValueError(f"secret-like field must not be persisted: {secret_path}")
        intent_json = _canonical_json(intent)
        intent_hash = hashlib.sha256(intent_json.encode()).hexdigest()
        now = _now()
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO operations "
                "(operation_id, origin_id, target_instance_key, target_repository_scope, "
                "operation_type, mapping_version, baseline_id, intent_json, intent_hash, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)",
                (
                    operation_id,
                    origin_id,
                    target_instance_key,
                    target_repository_scope,
                    operation_type,
                    mapping_version,
                    baseline_id,
                    intent_json,
                    intent_hash,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, created_at) "
                "VALUES (?, NULL, 'prepared', ?)",
                (operation_id, now),
            )
        except sqlite3.IntegrityError:
            existing = self.get_operation(operation_id)
            same = (
                existing.origin_id == origin_id
                and existing.target_instance_key == target_instance_key
                and existing.target_repository_scope == target_repository_scope
                and existing.operation_type == operation_type
                and existing.mapping_version == mapping_version
                and existing.baseline_id == baseline_id
                and existing.intent_hash == intent_hash
            )
            if not same:
                raise OperationConflict(
                    "operation id is already associated with a different intent"
                ) from None
            return existing
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def start_executor_session(self, owner_id: str, *, recover_abandoned: bool = False) -> None:
        _require_text(owner_id, "owner_id")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT owner_id FROM executor_lease WHERE singleton = 1"
            ).fetchone()
            if lease is not None and lease["owner_id"] != owner_id:
                if not recover_abandoned:
                    raise ExecutorLeaseConflict(f"executor lease is held by {lease['owner_id']}")
                previous_owner = str(lease["owner_id"])
                interrupted = list(
                    connection.execute(
                        "SELECT operation_id FROM operations "
                        "WHERE status = 'executing' AND executor_id = ?",
                        (previous_owner,),
                    )
                )
                reason = (
                    "previous executor session was interrupted after the external outcome "
                    "became uncertain; reconciliation required"
                )
                now = _now()
                for row in interrupted:
                    operation_id = str(row["operation_id"])
                    connection.execute(
                        "UPDATE operations SET status = 'outcome_unknown', error_reason = ?, "
                        "updated_at = ? WHERE operation_id = ?",
                        (reason, now, operation_id),
                    )
                    connection.execute(
                        "INSERT INTO operation_events "
                        "(operation_id, from_status, to_status, reason, created_at) "
                        "VALUES (?, 'executing', 'outcome_unknown', ?, ?)",
                        (operation_id, reason, now),
                    )
                    connection.execute(
                        "INSERT INTO conflicts VALUES (?, ?, 'outcome_unknown', ?, '{}', "
                        "'open', ?, NULL)",
                        (self.new_conflict_id(), operation_id, reason, now),
                    )
                connection.execute("DELETE FROM executor_lease WHERE singleton = 1")
            connection.execute(
                "INSERT OR REPLACE INTO executor_lease "
                "(singleton, owner_id, acquired_at) VALUES (1, ?, ?)",
                (owner_id, _now()),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def end_executor_session(self, owner_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM executor_lease WHERE singleton = 1 AND owner_id = ?", (owner_id,)
            )
        finally:
            connection.close()

    def claim_operation(self, operation_id: str, executor_id: str) -> OperationRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT owner_id FROM executor_lease WHERE singleton = 1"
            ).fetchone()
            if lease is None or lease["owner_id"] != executor_id:
                raise ExecutorLeaseConflict("executor does not own the durable writer lease")
            row = connection.execute(
                "SELECT status FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {operation_id}")
            status = str(row["status"])
            if status == "confirmed":
                connection.commit()
                return self.get_operation(operation_id)
            if status == "outcome_unknown":
                raise OperationBlocked(
                    f"operation {operation_id} has an unknown outcome; reconciliation required"
                )
            if status != "prepared":
                raise OperationBlocked(
                    f"operation {operation_id} cannot be claimed from status {status}"
                )
            now = _now()
            connection.execute(
                "UPDATE operations SET status = 'executing', executor_id = ?, updated_at = ? "
                "WHERE operation_id = ?",
                (executor_id, now, operation_id),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, created_at) "
                "VALUES (?, 'prepared', 'executing', ?)",
                (operation_id, now),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def block_operation_on_parent(self, operation_id: str, reason: str) -> OperationRecord:
        """Persist that no child POST was attempted because its parent is unresolved."""

        _require_text(reason, "reason")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {operation_id}")
            if row["status"] == "blocked_parent":
                connection.commit()
                return self.get_operation(operation_id)
            if row["status"] != "prepared":
                raise OperationBlocked(
                    f"operation {operation_id} cannot be parent-blocked from {row['status']}"
                )
            timestamp = _now()
            connection.execute(
                "UPDATE operations SET status = 'blocked_parent', error_reason = ?, "
                "updated_at = ? WHERE operation_id = ?",
                (reason, timestamp, operation_id),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, reason, created_at) "
                "VALUES (?, 'prepared', 'blocked_parent', ?, ?)",
                (operation_id, reason, timestamp),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def unblock_parent_operation(self, operation_id: str, parent_origin_id: str) -> OperationRecord:
        """Resume only after the exact persisted parent has a target mapping."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if operation is None:
                raise KeyError(f"unknown operation: {operation_id}")
            if operation["status"] != "blocked_parent":
                raise OperationBlocked(f"operation {operation_id} is not blocked on a parent")
            origin = connection.execute(
                "SELECT parent_origin_id FROM object_origins WHERE origin_id = ?",
                (operation["origin_id"],),
            ).fetchone()
            if origin is None or origin["parent_origin_id"] != parent_origin_id:
                raise MappingConflict("requested parent does not match immutable comment origin")
            mapping = connection.execute(
                "SELECT target_issue_index FROM object_mappings "
                "WHERE origin_id = ? AND target_instance_key = ?",
                (parent_origin_id, operation["target_instance_key"]),
            ).fetchone()
            if mapping is None or mapping["target_issue_index"] is None:
                raise OperationBlocked("parent has no confirmed target mapping")
            timestamp = _now()
            connection.execute(
                "UPDATE operations SET status = 'prepared', error_reason = NULL, updated_at = ? "
                "WHERE operation_id = ?",
                (timestamp, operation_id),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, reason, created_at) "
                "VALUES (?, 'blocked_parent', 'prepared', 'confirmed parent mapping', ?)",
                (operation_id, timestamp),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def unblock_operation_on_mappings(
        self, operation_id: str, parent_origin_ids: tuple[str, ...]
    ) -> OperationRecord:
        """Resume a provision step only after every named parent has a target mapping."""

        if not parent_origin_ids:
            raise ValueError("at least one parent origin is required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT target_instance_key, status FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if operation is None:
                raise KeyError(f"unknown operation: {operation_id}")
            if operation["status"] != "blocked_parent":
                raise OperationBlocked(f"operation {operation_id} is not blocked on a parent")
            for parent_origin_id in parent_origin_ids:
                mapping = connection.execute(
                    "SELECT 1 FROM object_mappings WHERE origin_id = ? AND target_instance_key = ?",
                    (parent_origin_id, operation["target_instance_key"]),
                ).fetchone()
                if mapping is None:
                    raise OperationBlocked(
                        f"parent origin {parent_origin_id} has no confirmed target mapping"
                    )
            timestamp = _now()
            connection.execute(
                "UPDATE operations SET status = 'prepared', error_reason = NULL, updated_at = ? "
                "WHERE operation_id = ?",
                (timestamp, operation_id),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, reason, created_at) "
                "VALUES (?, 'blocked_parent', 'prepared', 'confirmed parent mappings', ?)",
                (operation_id, timestamp),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def mark_outcome_unknown(self, operation_id: str, reason: str) -> OperationRecord:
        _require_text(reason, "reason")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {operation_id}")
            if row["status"] != "executing":
                raise OperationBlocked(
                    f"operation {operation_id} cannot become unknown from {row['status']}"
                )
            now = _now()
            connection.execute(
                "UPDATE operations SET status = 'outcome_unknown', error_reason = ?, "
                "updated_at = ? WHERE operation_id = ?",
                (reason, now, operation_id),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, reason, created_at) "
                "VALUES (?, 'executing', 'outcome_unknown', ?, ?)",
                (operation_id, reason, now),
            )
            connection.execute(
                "INSERT INTO conflicts VALUES (?, ?, 'outcome_unknown', ?, '{}', 'open', ?, NULL)",
                (self.new_conflict_id(), operation_id, reason, now),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def confirm_operation(
        self,
        operation_id: str,
        target: TargetConfirmation,
        before_commit: Any | None = None,
    ) -> OperationRecord:
        _require_text(target.target_global_id, "target_global_id")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if operation is None:
                raise KeyError(f"unknown operation: {operation_id}")
            if operation["status"] != "executing":
                raise OperationBlocked(
                    f"operation {operation_id} cannot be confirmed from {operation['status']}"
                )
            origin = connection.execute(
                "SELECT object_type FROM object_origins WHERE origin_id = ?",
                (operation["origin_id"],),
            ).fetchone()
            if origin is None:
                raise KeyError(f"unknown origin: {operation['origin_id']}")
            now = _now()
            if operation["operation_type"] in _MAPPING_PRESERVING_OPERATION_TYPES:
                mapping = connection.execute(
                    "SELECT * FROM object_mappings WHERE origin_id = ? AND target_instance_key = ?",
                    (operation["origin_id"], operation["target_instance_key"]),
                ).fetchone()
                if mapping is None or (
                    mapping["target_repository_scope"] != operation["target_repository_scope"]
                    or mapping["target_global_id"] != target.target_global_id
                    or mapping["target_issue_index"] != target.target_issue_index
                ):
                    raise MappingConflict(
                        "update confirmation does not match the immutable target mapping"
                    )
            else:
                connection.execute(
                    "INSERT INTO object_mappings "
                    "(origin_id, target_instance_key, target_repository_scope, "
                    "target_object_type, target_global_id, target_issue_index, "
                    "target_native_created_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation["origin_id"],
                        operation["target_instance_key"],
                        operation["target_repository_scope"],
                        origin["object_type"],
                        target.target_global_id,
                        target.target_issue_index,
                        target.target_native_created_at,
                        now,
                    ),
                )
            if before_commit is not None:
                before_commit()
            connection.execute(
                "UPDATE operations SET status = 'confirmed', target_global_id = ?, "
                "target_issue_index = ?, error_reason = NULL, updated_at = ? "
                "WHERE operation_id = ?",
                (target.target_global_id, target.target_issue_index, now, operation_id),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, created_at) "
                "VALUES (?, 'executing', 'confirmed', ?)",
                (operation_id, now),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def resolve_unknown_operation(
        self,
        operation_id: str,
        *,
        target_global_id: str,
        target_issue_index: int | None,
        observed_intent: Mapping[str, Any],
        evidence: str,
    ) -> OperationRecord:
        if not target_global_id:
            raise ValueError("a stable target global id is required for reconciliation")
        _require_text(evidence, "evidence")
        observed_json = _canonical_json(observed_intent)
        observed_hash = hashlib.sha256(observed_json.encode()).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if operation is None:
                raise KeyError(f"unknown operation: {operation_id}")
            if operation["status"] != "outcome_unknown":
                raise OperationBlocked(f"operation {operation_id} is not awaiting reconciliation")
            if observed_hash != operation["intent_hash"]:
                raise MappingConflict(
                    "observed target content does not exactly match the persisted intent"
                )
            now = _now()
            if operation["operation_type"] in _MAPPING_PRESERVING_OPERATION_TYPES:
                mapping = connection.execute(
                    "SELECT * FROM object_mappings WHERE origin_id = ? AND target_instance_key = ?",
                    (operation["origin_id"], operation["target_instance_key"]),
                ).fetchone()
                if mapping is None or (
                    mapping["target_repository_scope"] != operation["target_repository_scope"]
                    or mapping["target_global_id"] != target_global_id
                    or mapping["target_issue_index"] != target_issue_index
                ):
                    raise MappingConflict(
                        "reconciliation does not match the immutable target mapping"
                    )
            else:
                origin = connection.execute(
                    "SELECT object_type FROM object_origins WHERE origin_id = ?",
                    (operation["origin_id"],),
                ).fetchone()
                connection.execute(
                    "INSERT INTO object_mappings "
                    "(origin_id, target_instance_key, target_repository_scope, target_object_type, "
                    "target_global_id, target_issue_index, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation["origin_id"],
                        operation["target_instance_key"],
                        operation["target_repository_scope"],
                        origin["object_type"],
                        target_global_id,
                        target_issue_index,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE operations SET status = 'confirmed', target_global_id = ?, "
                "target_issue_index = ?, error_reason = NULL, updated_at = ? "
                "WHERE operation_id = ?",
                (target_global_id, target_issue_index, now, operation_id),
            )
            evidence_json = _canonical_json(
                {
                    "target_global_id": target_global_id,
                    "target_issue_index": target_issue_index,
                    "intent_hash": observed_hash,
                    "evidence": evidence,
                }
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, reason, evidence_json, created_at) "
                "VALUES (?, 'outcome_unknown', 'confirmed', 'explicit reconciliation', ?, ?)",
                (operation_id, evidence_json, now),
            )
            connection.execute(
                "UPDATE conflicts SET status = 'resolved', resolved_at = ? "
                "WHERE operation_id = ? AND conflict_type = 'outcome_unknown' "
                "AND status = 'open'",
                (now, operation_id),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def record_conflict(
        self,
        conflict_id: str,
        *,
        operation_id: str | None,
        conflict_type: str,
        reason: str,
        evidence: Mapping[str, Any],
    ) -> None:
        _require_text(conflict_id, "conflict_id")
        _require_text(conflict_type, "conflict_type")
        _require_text(reason, "reason")
        evidence_json = _canonical_json(evidence)
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO conflicts VALUES (?, ?, ?, ?, ?, 'open', ?, NULL)",
                (
                    conflict_id,
                    operation_id,
                    conflict_type,
                    reason,
                    evidence_json,
                    _now(),
                ),
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                "SELECT operation_id, conflict_type, reason, evidence_json "
                "FROM conflicts WHERE conflict_id = ?",
                (conflict_id,),
            ).fetchone()
            if existing is None or (
                existing["operation_id"] != operation_id
                or existing["conflict_type"] != conflict_type
                or existing["reason"] != reason
                or existing["evidence_json"] != evidence_json
            ):
                raise MappingConflict("conflict id must not be reused for other evidence") from None
        finally:
            connection.close()

    def mark_operation_conflict(
        self,
        operation_id: str,
        *,
        conflict_type: str,
        reason: str,
        evidence: Mapping[str, Any],
    ) -> OperationRecord:
        """Atomically halt a prepared operation and retain its conflict evidence."""

        _require_text(conflict_type, "conflict_type")
        _require_text(reason, "reason")
        timestamp = _now()
        evidence_json = _canonical_json(evidence)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown operation: {operation_id}")
            if row["status"] == "conflict":
                connection.commit()
                return self.get_operation(operation_id)
            if row["status"] != "prepared":
                raise OperationBlocked(
                    f"operation {operation_id} cannot conflict from {row['status']}"
                )
            connection.execute(
                "UPDATE operations SET status = 'conflict', error_reason = ?, updated_at = ? "
                "WHERE operation_id = ?",
                (reason, timestamp, operation_id),
            )
            connection.execute(
                "INSERT INTO operation_events "
                "(operation_id, from_status, to_status, reason, evidence_json, created_at) "
                "VALUES (?, 'prepared', 'conflict', ?, ?, ?)",
                (operation_id, reason, evidence_json, timestamp),
            )
            connection.execute(
                "INSERT INTO conflicts VALUES (?, ?, ?, ?, ?, 'open', ?, NULL)",
                (
                    self.new_conflict_id(),
                    operation_id,
                    conflict_type,
                    reason,
                    evidence_json,
                    timestamp,
                ),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_operation(operation_id)

    def operation_evidence(self, operation_id: str) -> dict[str, list[dict[str, Any]]]:
        connection = self._connect()
        try:
            events = [
                {
                    "from": row["from_status"],
                    "to": row["to_status"],
                    "reason": row["reason"],
                    "evidence": _decode_object(row["evidence_json"])
                    if row["evidence_json"]
                    else None,
                    "created_at": row["created_at"],
                }
                for row in connection.execute(
                    "SELECT from_status, to_status, reason, evidence_json, created_at "
                    "FROM operation_events WHERE operation_id = ? ORDER BY event_id",
                    (operation_id,),
                )
            ]
            conflicts = [
                {
                    "conflict_id": row["conflict_id"],
                    "type": row["conflict_type"],
                    "reason": row["reason"],
                    "evidence": _decode_object(row["evidence_json"]),
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "resolved_at": row["resolved_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM conflicts WHERE operation_id = ? ORDER BY created_at",
                    (operation_id,),
                )
            ]
            return {"events": events, "conflicts": conflicts}
        finally:
            connection.close()

    def get_operation(self, operation_id: str) -> OperationRecord:
        row = self._one("SELECT * FROM operations WHERE operation_id = ?", (operation_id,))
        return OperationRecord(
            row["operation_id"],
            row["origin_id"],
            row["target_instance_key"],
            row["target_repository_scope"],
            row["operation_type"],
            row["mapping_version"],
            row["baseline_id"],
            _decode_object(row["intent_json"]),
            row["intent_hash"],
            cast(OperationStatus, row["status"]),
            row["executor_id"],
            row["target_global_id"],
            row["target_issue_index"],
            row["error_reason"],
        )

    def get_received_manifest(self, manifest_id: str) -> dict[str, Any]:
        row = self._one(
            "SELECT * FROM transport_manifests WHERE manifest_id = ? AND direction = 'received'",
            (manifest_id,),
        )
        return {key: row[key] for key in row.keys() if key != "document_json"}

    def transport_identity_claims(self, manifest_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            return [
                {
                    "realm_key": row["realm_key"],
                    "login": row["source_login"],
                    "external_id": row["source_external_id"],
                    "ou_path": row["source_ou_path"],
                    "instance_key": row["related_instance_key"],
                    "account_id": row["related_account_id"],
                    "related_login": row["related_login"],
                    "status": row["claim_status"],
                }
                for row in connection.execute(
                    "SELECT * FROM transport_identity_claims WHERE manifest_id = ? "
                    "ORDER BY claim_index",
                    (manifest_id,),
                )
            ]
        finally:
            connection.close()

    def _one(self, query: str, parameters: tuple[Any, ...]) -> sqlite3.Row:
        connection = self._connect()
        try:
            row = connection.execute(query, parameters).fetchone()
            if row is None:
                raise KeyError(f"state record not found for query: {query}")
            return cast(sqlite3.Row, row)
        finally:
            connection.close()

    @staticmethod
    def new_conflict_id() -> str:
        return str(uuid.uuid4())
