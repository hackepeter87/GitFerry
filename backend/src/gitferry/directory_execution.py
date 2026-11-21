"""Durable, target-local journal for directory bootstrap execution.

Schema version 15 owns this journal. It does not mutate transport/object-origin
tables and never retries an uncertain external command.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gitferry.operations import OperationBlocked, OperationExecutor
from gitferry.state_codec import now
from gitferry.state_store import StateStore

_DECIMAL_ID = re.compile(r"^[0-9]+$")
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class DirectoryBootstrapRecord:
    instance_key: str
    source_name: str
    realm_key: str
    intent_digest: str
    config_fingerprint: str
    status: str
    native_source_id: str | None
    reason: str | None
    created_at: str
    updated_at: str


class DirectoryBootstrapConflict(RuntimeError):
    """An immutable directory bootstrap key was reused with changed intent."""


class DirectoryBootstrapUnknown(OperationBlocked):
    """An external directory bootstrap may have happened and needs evidence."""


Runner = Callable[[DirectoryBootstrapRecord], str]
Verifier = Callable[[DirectoryBootstrapRecord], bool]


class DirectoryBootstrapService:
    def __init__(self, state: StateStore):
        self.state = state

    @staticmethod
    def _validate_inputs(
        instance_key: str,
        realm_key: str,
        source_name: str,
        intent_digest: str,
        config_fingerprint: str,
    ) -> None:
        for label, value in (
            ("instance key", instance_key),
            ("realm key", realm_key),
            ("source name", source_name),
        ):
            if not isinstance(value, str) or not _NAME.fullmatch(value):
                raise ValueError(f"invalid directory bootstrap {label}")
        for label, value in (
            ("intent digest", intent_digest),
            ("config fingerprint", config_fingerprint),
        ):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"invalid directory bootstrap {label}")

    def _record(
        self, connection: sqlite3.Connection, instance_key: str, source_name: str
    ) -> DirectoryBootstrapRecord | None:
        row = connection.execute(
            "SELECT * FROM directory_bootstraps WHERE instance_key=? AND source_name=?",
            (instance_key, source_name),
        ).fetchone()
        if row is None:
            return None
        return DirectoryBootstrapRecord(*(row[key] for key in row.keys()))

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        record: DirectoryBootstrapRecord,
        old: str | None,
        reason: str | None,
    ) -> None:
        connection.execute(
            "INSERT INTO directory_bootstrap_events "
            "(instance_key,source_name,from_status,to_status,reason,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                record.instance_key,
                record.source_name,
                old,
                record.status,
                reason,
                record.updated_at,
            ),
        )

    def get(self, instance_key: str, source_name: str) -> DirectoryBootstrapRecord | None:
        connection = self.state._connect()
        try:
            return self._record(connection, instance_key, source_name)
        finally:
            connection.close()

    def prepare(
        self,
        instance_key: str,
        realm_key: str,
        source_name: str,
        intent_digest: str,
        config_fingerprint: str,
    ) -> DirectoryBootstrapRecord:
        self._validate_inputs(
            instance_key, realm_key, source_name, intent_digest, config_fingerprint
        )
        connection = self.state._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._record(connection, instance_key, source_name)
            if current is not None:
                changed = (
                    current.realm_key,
                    current.intent_digest,
                    current.config_fingerprint,
                ) != (realm_key, intent_digest, config_fingerprint)
                if changed and current.status in {"executing", "outcome_unknown"}:
                    connection.commit()
                    raise DirectoryBootstrapConflict(
                        "directory bootstrap uncertainty cannot be replaced"
                    )
                if changed:
                    updated = now()
                    connection.execute(
                        "UPDATE directory_bootstraps SET status='conflict', reason=?, updated_at=? "
                        "WHERE instance_key=? AND source_name=?",
                        ("directory bootstrap intent changed", updated, instance_key, source_name),
                    )
                    changed_record = self._record(connection, instance_key, source_name)
                    if changed_record is None:
                        raise RuntimeError("directory bootstrap row disappeared")
                    self._event(
                        connection,
                        changed_record,
                        current.status,
                        "directory bootstrap intent changed",
                    )
                    connection.commit()
                    raise DirectoryBootstrapConflict("directory bootstrap intent changed")
                connection.commit()
                return current
            timestamp = now()
            connection.execute(
                "INSERT INTO directory_bootstraps "
                "(instance_key,source_name,realm_key,intent_digest,config_fingerprint,"
                "status,native_source_id,reason,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    instance_key,
                    source_name,
                    realm_key,
                    intent_digest,
                    config_fingerprint,
                    "prepared",
                    None,
                    None,
                    timestamp,
                    timestamp,
                ),
            )
            created = self._record(connection, instance_key, source_name)
            if created is None:
                raise RuntimeError("directory bootstrap row was not created")
            self._event(connection, created, None, None)
            connection.commit()
            return created
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _transition(
        self,
        record: DirectoryBootstrapRecord,
        status: str,
        *,
        native_id: str | None = None,
        reason: str | None = None,
    ) -> DirectoryBootstrapRecord:
        connection = self.state._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = now()
            cursor = connection.execute(
                "UPDATE directory_bootstraps SET status=?, "
                "native_source_id=COALESCE(?,native_source_id), reason=?, updated_at=? "
                "WHERE instance_key=? AND source_name=? AND realm_key=? "
                "AND intent_digest=? AND config_fingerprint=? AND status=?",
                (
                    status,
                    native_id,
                    reason,
                    timestamp,
                    record.instance_key,
                    record.source_name,
                    record.realm_key,
                    record.intent_digest,
                    record.config_fingerprint,
                    record.status,
                ),
            )
            if cursor.rowcount != 1:
                raise DirectoryBootstrapConflict("directory bootstrap state changed")
            updated = self._record(connection, record.instance_key, record.source_name)
            if updated is None:
                raise RuntimeError("directory bootstrap row disappeared")
            self._event(connection, updated, record.status, reason)
            connection.commit()
            return updated
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def apply(
        self,
        record: DirectoryBootstrapRecord,
        runner: Runner,
        verify: Verifier | None = None,
    ) -> DirectoryBootstrapRecord:
        with OperationExecutor(self.state, f"directory-{record.instance_key}-{record.source_name}"):
            current = self.get(record.instance_key, record.source_name)
            if current is None:
                raise DirectoryBootstrapConflict("directory bootstrap record is missing")
            if (
                current.realm_key,
                current.intent_digest,
                current.config_fingerprint,
            ) != (record.realm_key, record.intent_digest, record.config_fingerprint):
                raise DirectoryBootstrapConflict("directory bootstrap intent is stale")
            if current.status == "executing":
                self._transition(
                    current,
                    "outcome_unknown",
                    reason="directory bootstrap execution interrupted",
                )
                raise DirectoryBootstrapUnknown("directory bootstrap outcome is unknown")
            if current.status == "outcome_unknown":
                raise DirectoryBootstrapUnknown("directory bootstrap outcome is unknown")
            if current.status == "conflict":
                raise DirectoryBootstrapConflict("directory bootstrap is conflicted")
            if current.status == "confirmed":
                if verify is None:
                    raise DirectoryBootstrapConflict(
                        "confirmed directory source requires readback verification"
                    )
                try:
                    verified = verify(current)
                except Exception as exc:
                    raise DirectoryBootstrapConflict(
                        "confirmed directory source readback failed"
                    ) from exc
                if not verified:
                    raise DirectoryBootstrapConflict("confirmed directory source readback changed")
                return current
            executing = self._transition(current, "executing")
            try:
                native_id = runner(executing)
                if not isinstance(native_id, str) or not _DECIMAL_ID.fullmatch(native_id):
                    raise ValueError("native source id must be a decimal string")
            except Exception as exc:
                self._transition(
                    executing,
                    "outcome_unknown",
                    reason="directory bootstrap execution failed",
                )
                raise DirectoryBootstrapUnknown("directory bootstrap outcome is unknown") from exc
            return self._transition(executing, "confirmed", native_id=native_id)

    def events(self, instance_key: str, source_name: str) -> list[Mapping[str, Any]]:
        connection = self.state._connect()
        try:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM directory_bootstrap_events "
                    "WHERE instance_key=? AND source_name=? ORDER BY event_id",
                    (instance_key, source_name),
                )
            ]
        finally:
            connection.close()


__all__ = [
    "DirectoryBootstrapRecord",
    "DirectoryBootstrapService",
    "DirectoryBootstrapConflict",
    "DirectoryBootstrapUnknown",
]
