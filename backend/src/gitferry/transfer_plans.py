"""Durable, immutable execution plans built from the operations journal."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any, cast

from gitferry.plan_diagnostics import PreflightCheck
from gitferry.state_codec import find_secret_field, now
from gitferry.state_store import StateStore


class PlanConflict(ValueError):
    """The requested plan is invalid, stale, or has an incompatible state."""


_TYPES = frozenset(
    {
        "create_organization",
        "create_repository",
        "create_team",
        "assign_team_repository",
        "add_team_member",
        "push_git_bundle",
        "create_issue",
        "update_issue",
        "create_comment",
        "update_comment",
    }
)
_STATUSES = frozenset(
    {"draft", "queued", "preflighting", "running", "confirmed", "blocked", "failed", "cancelled"}
)
_TRANSITIONS = {
    "draft": {"queued", "cancelled"},
    "queued": {"preflighting", "blocked", "cancelled"},
    "preflighting": {"running", "blocked", "failed", "cancelled"},
    "running": {"preflighting", "confirmed", "blocked", "failed", "cancelled"},
    "confirmed": set(),
    "blocked": {"queued"},
    "failed": {"queued"},
    "cancelled": {"queued"},
}


def _json(value: Any) -> str:
    try:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        if len(text.encode()) > 1024 * 1024:
            raise PlanConflict("plan document exceeds 1 MiB")
        if json.loads(text) != value:
            raise PlanConflict("plan document does not round-trip")
        if find_secret_field(value):
            raise PlanConflict("secret-like fields cannot be persisted")
        return text
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        if isinstance(exc, PlanConflict):
            raise
        raise PlanConflict("plan document must be finite JSON") from None


def _digest(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(document).encode()).hexdigest()


class PlanService:
    def __init__(self, store: StateStore):
        self.store = store

    @staticmethod
    def _profile(c: sqlite3.Connection, key: str, revision: int, digest: str) -> None:
        profile = c.execute(
            "SELECT * FROM connection_profiles WHERE instance_key=?", (key,)
        ).fetchone()
        check = c.execute("SELECT * FROM connection_checks WHERE instance_key=?", (key,)).fetchone()
        if (
            profile is None
            or not profile["enabled"]
            or not profile["locally_approved"]
            or profile["revision"] != revision
            or check is None
            or check["profile_revision"] != revision
            or not check["approved_identity"]
            or check["observed_identity"] != check["approved_identity"]
            or hashlib.sha256(check["approved_identity"].encode()).hexdigest() != digest
        ):
            raise PlanConflict("profile revision or approval evidence changed; review a new plan")

    @staticmethod
    def _snapshot(c: sqlite3.Connection, identifier: str) -> dict[str, Any]:
        row = c.execute("SELECT * FROM operations WHERE operation_id=?", (identifier,)).fetchone()
        if row is None or row["operation_type"] not in _TYPES:
            raise PlanConflict("operation is unknown or unsupported")
        result = {
            key: row[key]
            for key in (
                "origin_id",
                "target_instance_key",
                "target_repository_scope",
                "operation_type",
                "mapping_version",
                "baseline_id",
                "intent_hash",
            )
        }
        result["intent"] = json.loads(row["intent_json"])
        if hashlib.sha256(row["intent_json"].encode()).hexdigest() != row["intent_hash"]:
            raise PlanConflict("journal intent digest mismatch")
        result["baseline"] = None
        if row["baseline_id"] is not None:
            baseline = c.execute(
                "SELECT * FROM baselines WHERE baseline_id=?", (row["baseline_id"],)
            ).fetchone()
            if baseline is None:
                raise PlanConflict("operation baseline is missing")
            result["baseline"] = {
                key: baseline[key]
                for key in (
                    "baseline_id",
                    "transfer_round_id",
                    "origin_id",
                    "ruleset_name",
                    "ruleset_version",
                    "origin_created_at",
                    "target_native_created_at",
                )
            }
            result["baseline"]["values"] = json.loads(baseline["baseline_json"])
        result["bindings"] = {}
        for field in ("author_binding_id", "binding_id"):
            binding_id = result["intent"].get(field)
            if binding_id is not None:
                binding = c.execute(
                    "SELECT * FROM identity_bindings WHERE binding_id=?", (binding_id,)
                ).fetchone()
                if binding is None or binding["target_instance_key"] != row["target_instance_key"]:
                    raise PlanConflict(
                        "local identity binding is missing or targets another instance"
                    )
                result["bindings"][field] = dict(binding)
        return result

    def _validate(self, c: sqlite3.Connection, row: sqlite3.Row) -> None:
        self._profile(
            c, row["target_instance_key"], row["profile_revision"], row["evidence_digest"]
        )
        plan = self._read(c, row["plan_id"])
        for snapshot in plan["document"]["operations"]:
            expected = {key: value for key, value in snapshot.items() if key != "operation_id"}
            if self._snapshot(c, snapshot["operation_id"]) != expected:
                raise PlanConflict(
                    "operation intent, identity binding or immutable baseline changed"
                )

    @staticmethod
    def _row(
        row: sqlite3.Row,
        operations: list[dict[str, Any]],
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = {
            "plan_id": row["plan_id"],
            "digest": row["digest"],
            "target_instance_key": row["target_instance_key"],
            "profile_revision": row["profile_revision"],
            "evidence_digest": row["evidence_digest"],
            "round_key": row["round_key"],
            "document": json.loads(row["document_json"]),
            "status": row["status"],
            "cancel_requested": bool(row["cancel_requested"]),
            "reason": row["reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "operations": operations,
        }
        if events is not None:
            result["events"] = events
        return result

    @staticmethod
    def _validate_graph(ids: list[str], dependencies: Mapping[str, list[str]]) -> list[str]:
        if set(dependencies) != set(ids) or any(
            not isinstance(v, list) or any(x not in ids for x in v) for v in dependencies.values()
        ):
            raise PlanConflict("dependencies must name exactly the plan operations")
        indegree = {x: len(set(dependencies[x])) for x in ids}
        children: dict[str, list[str]] = {x: [] for x in ids}
        for child, parents in dependencies.items():
            for parent in set(parents):
                children[parent].append(child)
        ready = sorted(x for x, n in indegree.items() if n == 0)
        order = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in sorted(children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(order) != len(ids):
            raise PlanConflict("operation dependencies contain a cycle")
        return order

    def _get_row(self, connection: sqlite3.Connection, plan_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM transfer_plans WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if row is None:
            raise PlanConflict("unknown transfer plan")
        return cast(sqlite3.Row, row)

    def _read(
        self, connection: sqlite3.Connection, plan_id: str, include_events: bool = True
    ) -> dict[str, Any]:
        row = self._get_row(connection, plan_id)
        document = json.loads(row["document_json"])
        if _digest(document) != row["digest"] or row["plan_id"] != row["digest"]:
            raise PlanConflict("stored plan integrity check failed")
        if any(
            row[key] != document[key]
            for key in ("target_instance_key", "profile_revision", "evidence_digest", "round_key")
        ):
            raise PlanConflict("plan routing columns do not match the immutable document")
        steps = list(
            connection.execute(
                "SELECT * FROM plan_steps WHERE plan_id=? ORDER BY step_index", (plan_id,)
            )
        )
        if len(steps) != len(document["operations"]) or any(
            {"operation_id": step["operation_id"], **json.loads(step["snapshot_json"])} != expected
            for step, expected in zip(steps, document["operations"])
        ):
            raise PlanConflict("plan step references do not match the immutable document")
        ops = [
            json.loads(x["snapshot_json"])
            | {
                "operation_id": x["operation_id"],
                "step_index": x["step_index"],
                "status": x["status"],
                "target_global_id": x["target_global_id"],
                "error_reason": x["error_reason"],
            }
            for x in connection.execute(
                "SELECT s.*, o.status, o.target_global_id, o.error_reason FROM plan_steps s "
                "JOIN operations o USING(operation_id) WHERE s.plan_id=? ORDER BY step_index",
                (plan_id,),
            )
        ]
        events = None
        if include_events:
            events = [
                dict(x)
                for x in connection.execute(
                    "SELECT * FROM plan_events WHERE plan_id=? ORDER BY event_id", (plan_id,)
                )
            ]
        result = self._row(row, ops, events)
        result["preview"] = self._latest_preview(connection, plan_id)
        return result

    def create(
        self,
        *,
        target_instance_key: str,
        profile_revision: int,
        evidence_digest: str,
        round_key: str,
        inventory: dict[str, Any],
        operation_ids: list[str],
        dependencies: dict[str, list[str]],
        required_bytes: int = 0,
    ) -> dict[str, Any]:
        if (
            type(profile_revision) is not int
            or profile_revision < 1
            or not isinstance(evidence_digest, str)
            or len(evidence_digest) != 64
        ):
            raise PlanConflict("invalid profile evidence")
        if type(required_bytes) is not int or required_bytes < 0:
            raise PlanConflict("invalid required storage")
        if not isinstance(round_key, str) or not round_key.strip() or len(round_key) > 128:
            raise PlanConflict("explicit bounded transfer round required")
        if not isinstance(inventory, dict) or len(operation_ids) > 10000:
            raise PlanConflict("invalid inventory or excessive operation count")
        if len(set(operation_ids)) != len(operation_ids) or not operation_ids:
            raise PlanConflict("operation ids must be unique")
        if find_secret_field(inventory):
            raise PlanConflict("secret-like field must not be persisted")
        order = self._validate_graph(operation_ids, dependencies)
        with self.store.transaction() as c:
            self._profile(c, target_instance_key, profile_revision, evidence_digest)
            snapshots = {oid: self._snapshot(c, oid) for oid in operation_ids}
            if any(
                item["target_instance_key"] != target_instance_key for item in snapshots.values()
            ):
                raise PlanConflict("operation targets another instance")
            document = {
                "version": 1,
                "target_instance_key": target_instance_key,
                "profile_revision": profile_revision,
                "evidence_digest": evidence_digest,
                "round_key": round_key,
                "inventory": inventory,
                "operations": [{"operation_id": oid, **snapshots[oid]} for oid in order],
                "dependencies": {k: sorted(set(dependencies[k])) for k in sorted(dependencies)},
                "required_bytes": required_bytes,
            }
            digest = _digest(document)
            plan_id = digest
            existing = c.execute(
                "SELECT * FROM transfer_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if existing:
                return self._read(c, plan_id)
            timestamp = now()
            c.execute(
                "INSERT INTO transfer_plans (plan_id,digest,target_instance_key,profile_revision,"
                "evidence_digest,round_key,document_json,status,reason,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    plan_id,
                    digest,
                    target_instance_key,
                    profile_revision,
                    evidence_digest,
                    round_key,
                    _json(document),
                    "draft",
                    None,
                    timestamp,
                    timestamp,
                ),
            )
            for index, oid in enumerate(order):
                c.execute(
                    "INSERT INTO plan_steps VALUES (?,?,?,?)",
                    (plan_id, index, oid, _json(snapshots[oid])),
                )
            c.execute(
                "INSERT INTO plan_events(plan_id,to_status,created_at) VALUES (?,?,?)",
                (plan_id, "draft", timestamp),
            )
            return self._read(c, plan_id)

    def get(self, plan_id: str) -> dict[str, Any]:
        with self.store.read_connection() as c:
            return self._read(c, plan_id)

    def list(self) -> list[dict[str, Any]]:
        with self.store.read_connection() as c:
            return [
                self._read(c, r["plan_id"])
                for r in c.execute(
                    "SELECT plan_id FROM transfer_plans ORDER BY created_at, plan_id"
                )
            ]

    def _transition_row(
        self,
        c: sqlite3.Connection,
        row: sqlite3.Row,
        status: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        old, plan_id = row["status"], row["plan_id"]
        if status not in _STATUSES:
            raise PlanConflict("invalid plan status")
        if status == old:
            return self._read(c, plan_id)
        if status not in _TRANSITIONS[old]:
            raise PlanConflict(f"invalid plan transition {old} -> {status}")
        if (
            status == "confirmed"
            and c.execute(
                "SELECT 1 FROM plan_steps s JOIN operations o USING(operation_id) "
                "WHERE s.plan_id=? AND o.status<>'confirmed' LIMIT 1",
                (plan_id,),
            ).fetchone()
        ):
            raise PlanConflict("unconfirmed operations cannot be reported as complete")
        timestamp = now()
        c.execute(
            "UPDATE transfer_plans SET status=?,reason=?,updated_at=? WHERE plan_id=?",
            (status, reason, timestamp, plan_id),
        )
        c.execute(
            "INSERT INTO plan_events(plan_id,from_status,to_status,reason,created_at) "
            "VALUES (?,?,?,?,?)",
            (plan_id, old, status, reason, timestamp),
        )
        return self._read(c, plan_id)

    @staticmethod
    def _latest_preview(c: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
        row = c.execute(
            "SELECT * FROM plan_previews WHERE plan_id=? ORDER BY generation DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _journal_blocked(c: sqlite3.Connection, plan_id: str) -> bool:
        return (
            c.execute(
                "SELECT 1 FROM plan_steps s JOIN operations selected USING(operation_id) "
                "JOIN operations other ON other.origin_id=selected.origin_id "
                "AND other.target_instance_key=selected.target_instance_key "
                "WHERE s.plan_id=? AND other.status IN "
                "('executing','outcome_unknown','conflict','failed') LIMIT 1",
                (plan_id,),
            ).fetchone()
            is not None
        )

    def request_preview(self, plan_id: str, digest: str) -> dict[str, Any]:
        """Persist read-only target work; this never authorizes execution."""
        with self.store.transaction() as c:
            row = self._get_row(c, plan_id)
            if digest != row["digest"] or row["status"] not in {
                "draft",
                "blocked",
                "failed",
                "cancelled",
            }:
                raise PlanConflict("plan is not available for prerequisite review")
            self._validate(c, row)
            previous = self._latest_preview(c, plan_id)
            if previous and previous["status"] in {"queued", "checking"}:
                return self._read(c, plan_id)
            generation = previous["generation"] + 1 if previous else 1
            timestamp = now()
            c.execute(
                "INSERT INTO plan_previews VALUES (?,?,?,'queued',NULL,NULL,?,?)",
                (plan_id, generation, digest, timestamp, timestamp),
            )
            return self._read(c, plan_id)

    def begin_preview(self, plan_id: str) -> dict[str, Any]:
        """Worker holds the existing executor lock; interrupted reads are safe to repeat."""
        with self.store.transaction() as c:
            row = self._get_row(c, plan_id)
            preview = self._latest_preview(c, plan_id)
            if not preview or preview["status"] not in {"queued", "checking"}:
                raise PlanConflict("no pending prerequisite review")
            reason = None
            try:
                self._validate(c, row)
                if row["status"] not in {"draft", "blocked", "failed", "cancelled"}:
                    raise PlanConflict("plan execution state changed")
            except PlanConflict:
                reason = PreflightCheck.SELECTION.value
            if self._journal_blocked(c, plan_id):
                reason = PreflightCheck.JOURNAL.value
            c.execute(
                "UPDATE plan_previews SET status=?,reason=?,scope=NULL,updated_at=? "
                "WHERE plan_id=? AND generation=?",
                (
                    "blocked" if reason else "checking",
                    reason,
                    now(),
                    plan_id,
                    preview["generation"],
                ),
            )
            return self._read(c, plan_id)

    def finish_preview(
        self,
        plan_id: str,
        generation: int,
        *,
        check: PreflightCheck | None = None,
        scope: str | None = None,
        failed: bool = False,
    ) -> dict[str, Any]:
        """Worker-only result; never expose a caller-supplied success override."""
        with self.store.transaction() as c:
            row = self._get_row(c, plan_id)
            preview = self._latest_preview(c, plan_id)
            if (
                not preview
                or preview["generation"] != generation
                or preview["status"] != "checking"
            ):
                raise PlanConflict("prerequisite review changed")
            try:
                self._validate(c, row)
                if row["status"] not in {"draft", "blocked", "failed", "cancelled"}:
                    raise PlanConflict("plan execution state changed")
            except PlanConflict:
                check = PreflightCheck.SELECTION
            if check is None and self._journal_blocked(c, plan_id):
                check = PreflightCheck.JOURNAL
            allowed_scopes = {
                step["target_repository_scope"] for step in self._read(c, plan_id)["operations"]
            }
            scope = scope if scope in allowed_scopes else None
            status = "failed" if failed else "blocked" if check else "passed"
            reason = (
                "local prerequisite check failed; inspect local service availability"
                if failed
                else check.value
                if check
                else None
            )
            c.execute(
                "UPDATE plan_previews SET status=?,reason=?,scope=?,updated_at=? "
                "WHERE plan_id=? AND generation=?",
                (status, reason, scope, now(), plan_id, generation),
            )
            return self._read(c, plan_id)

    def queue(
        self,
        plan_id: str,
        digest: str,
        *,
        preview_generation: int | None = None,
    ) -> dict[str, Any]:
        with self.store.transaction() as c:
            row = self._get_row(c, plan_id)
            if digest != row["digest"]:
                raise PlanConflict("plan digest mismatch")
            if row["status"] == "confirmed":
                return self._read(c, plan_id)
            if preview_generation is not None:
                preview = self._latest_preview(c, plan_id)
                if (
                    type(preview_generation) is not int
                    or not preview
                    or preview["generation"] != preview_generation
                    or preview["status"] != "passed"
                    or preview["plan_digest"] != digest
                    or self._journal_blocked(c, plan_id)
                ):
                    raise PlanConflict("review the current target prerequisite result first")
            self._validate(c, row)
            result = self._transition_row(c, row, "queued")
            c.execute("UPDATE transfer_plans SET cancel_requested=0 WHERE plan_id=?", (plan_id,))
            result["cancel_requested"] = False
            return result

    def cancel(self, plan_id: str) -> dict[str, Any]:
        with self.store.transaction() as c:
            row = self._get_row(c, plan_id)
            if row["status"] in ("confirmed", "cancelled", "blocked", "failed"):
                return self._read(c, plan_id)
            c.execute(
                "UPDATE transfer_plans SET cancel_requested=1,updated_at=? WHERE plan_id=?",
                (now(), plan_id),
            )
            if row["status"] in ("draft", "queued"):
                return self._transition_row(c, row, "cancelled", "cancelled before execution")
            c.execute(
                "INSERT INTO plan_events(plan_id,from_status,to_status,reason,created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    plan_id,
                    row["status"],
                    row["status"],
                    "cancel requested at next safe boundary",
                    now(),
                ),
            )
            return self._read(c, plan_id)

    def begin(self, plan_id: str) -> dict[str, Any]:
        """Worker must hold OperationExecutor's existing process lock before this call."""
        with self.store.transaction() as c:
            row = self._get_row(c, plan_id)
            if row["status"] not in {"queued", "preflighting", "running"}:
                raise PlanConflict("plan is not executable")
            if row["cancel_requested"]:
                return self._transition_row(c, row, "cancelled", "cancelled at safe boundary")
            try:
                self._validate(c, row)
            except PlanConflict as exc:
                return self._transition_row(c, row, "blocked", str(exc))
            if self._journal_blocked(c, plan_id):
                return self._transition_row(
                    c, row, "blocked", "operation journal requires reconciliation"
                )
            return self._transition_row(c, row, "preflighting")

    def finish(self, plan_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
        """Worker-only transition; no API/CLI endpoint may expose a success override."""
        with self.store.transaction() as c:
            return self._transition_row(c, self._get_row(c, plan_id), status, reason)

    def record_event(
        self, plan_id: str, reason: str, evidence: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        with self.store.transaction() as c:
            row = self._get_row(c, plan_id)
            c.execute(
                "INSERT INTO plan_events(plan_id,from_status,to_status,reason,"
                "evidence_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (plan_id, row["status"], row["status"], reason, _json(evidence or {}), now()),
            )
            return self._read(c, plan_id)


__all__ = ["PlanConflict", "PlanService"]
