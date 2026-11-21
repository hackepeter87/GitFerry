"""Durable, local identity candidates and their append-only decisions.

This module records review state only.  It never talks to Gitea and never stores a
credential.  A caller that has independently verified a target may provide ``binder``
to :meth:`IdentityDecisionService.decide`; the callback receives the open transaction
and is the only place where an existing identity binding should be created.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any, Literal

from gitferry.state_codec import canonical_json, find_secret_field, now, require_text
from gitferry.state_store import BindingConflict, StateStore

Decision = Literal["pending", "rejected", "approved", "conflict"]


class IdentityDecisionError(RuntimeError):
    """Base error for invalid or conflicting local identity decisions."""


class IdentityDecisionStale(IdentityDecisionError):
    """The caller attempted to decide an old revision."""


class IdentityDecisionConflict(IdentityDecisionError):
    """An immutable candidate or binding would be redirected."""


_PREREQUISITES = ("issue_author", "comment_author", "team_member")
_TARGET_KEYS = ("instance_key", "account_id", "login", "external_id", "ou_path", "native_id")


def _text(
    value: Any, field: str, *, optional: bool = False, allow_empty: bool = False
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    if not (allow_empty and value == ""):
        require_text(value, field)
    if len(value) > 2048:
        raise ValueError(f"{field} is too long")
    return value


class IdentityDecisionService:
    """Short-transaction service for identity review candidates."""

    def __init__(self, state: StateStore):
        self.state = state

    @staticmethod
    def _candidate_id(selector: Mapping[str, Any], target: Mapping[str, Any]) -> str:
        identity = {
            "selector": dict(selector),
            "target": {key: target[key] for key in ("instance_key", "account_id")},
        }
        return (
            "identity-candidate-"
            + hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:48]
        )

    @staticmethod
    def _validate_selector(selector: Mapping[str, Any]) -> dict[str, str]:
        expected = ("realm_key", "login", "external_id", "ou_path")
        if set(selector) != set(expected):
            raise ValueError("selector must contain realm_key, login, external_id, and ou_path")
        result = {
            key: _text(selector[key], f"selector.{key}", allow_empty=key == "ou_path")
            for key in expected
        }
        return {key: str(result[key]) for key in expected}

    @staticmethod
    def _validate_target(target: Mapping[str, Any]) -> dict[str, Any]:
        required = ("instance_key", "account_id", "login")
        if any(key not in target for key in required):
            raise ValueError("target must contain instance_key, account_id, and login")
        if set(target) - set(_TARGET_KEYS):
            raise ValueError("target contains an unsupported field")
        instance_key = _text(target["instance_key"], "target.instance_key")
        login = _text(target["login"], "target.login")
        account_id = target["account_id"]
        if type(account_id) is not int or account_id <= 0:
            raise ValueError("target.account_id must be a positive integer")
        return {
            "instance_key": instance_key,
            "account_id": account_id,
            "login": login,
            "external_id": _text(target.get("external_id"), "target.external_id", optional=True),
            "ou_path": _text(target.get("ou_path"), "target.ou_path", optional=True),
            "native_id": _text(target.get("native_id"), "target.native_id", optional=True),
        }

    @staticmethod
    def _validate_prerequisites(value: Mapping[str, Any]) -> dict[str, bool]:
        if set(value) != set(_PREREQUISITES) or any(type(value[key]) is not bool for key in value):
            raise ValueError(
                "prerequisites must contain boolean issue_author, comment_author, and team_member"
            )
        return {key: value[key] for key in _PREREQUISITES}

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        import json

        return {
            "candidate_id": row["candidate_id"],
            "selector": {
                "realm_key": row["realm_key"],
                "login": row["source_login"],
                "external_id": row["source_external_id"],
                "ou_path": row["source_ou_path"],
            },
            "email": row["source_email"],
            "target": {
                "instance_key": row["target_instance_key"],
                "account_id": str(row["target_account_id"]),
                "login": row["target_login"],
                "external_id": row["target_external_id"],
                "ou_path": row["target_ou_path"],
                "native_id": row["target_native_id"],
            },
            "authority_digest": row["authority_digest"],
            "profile_revision": row["profile_revision"],
            "prerequisites": json.loads(row["prerequisites_json"]),
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def submit_candidate(
        self,
        *,
        selector: Mapping[str, Any],
        target: Mapping[str, Any],
        prerequisites: Mapping[str, Any],
        authority_digest: str,
        profile_revision: int,
        email: str | None = None,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        selector = self._validate_selector(selector)
        target = self._validate_target(target)
        prerequisites = self._validate_prerequisites(prerequisites)
        email = _text(email, "email", optional=True)
        _text(authority_digest, "authority_digest")
        if not re.fullmatch(r"[0-9a-f]{64}", str(authority_digest)):
            raise ValueError("authority_digest must be a lowercase SHA-256 digest")
        if type(profile_revision) is not int or profile_revision < 1:
            raise ValueError("profile_revision must be positive")
        candidate_id = candidate_id or self._candidate_id(selector, target)
        _text(candidate_id, "candidate_id")
        payload = {
            "selector": selector,
            "target": target,
            "prerequisites": prerequisites,
            "email": email,
        }
        if find_secret_field(payload):
            raise ValueError("identity candidate contains a secret-like field")
        self.state.get_realm(selector["realm_key"])
        self.state.get_instance(target["instance_key"])
        with self.state.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM identity_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row:
                existing = self._record(row)
                normalized_target = {**target, "account_id": str(target["account_id"])}
                if (
                    existing["selector"] != selector
                    or existing["target"] != normalized_target
                    or existing["email"] != email
                    or existing["prerequisites"] != prerequisites
                    or existing["authority_digest"] != authority_digest
                    or existing["profile_revision"] != profile_revision
                ):
                    raise IdentityDecisionConflict("identity candidate must not be redirected")
                return existing
            timestamp = now()
            connection.execute(
                "INSERT INTO identity_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    selector["realm_key"],
                    selector["login"],
                    selector["external_id"],
                    selector["ou_path"],
                    email,
                    target["instance_key"],
                    target["account_id"],
                    target["login"],
                    target["external_id"],
                    target["ou_path"],
                    target["native_id"],
                    authority_digest,
                    profile_revision,
                    canonical_json(prerequisites),
                    "pending",
                    1,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "INSERT INTO identity_decision_events VALUES (NULL,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    1,
                    "pending",
                    "system",
                    "candidate-created",
                    canonical_json(payload),
                    timestamp,
                ),
            )
            return self._record(
                connection.execute(
                    "SELECT * FROM identity_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
            )

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self.state.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM identity_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown identity candidate: {candidate_id}")
        return self._record(row)

    def list_candidates(self, *, status: Decision | None = None) -> list[dict[str, Any]]:
        with self.state.read_connection() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM identity_candidates ORDER BY created_at, candidate_id"
                ).fetchall()
            else:
                if status not in {"pending", "rejected", "approved", "conflict"}:
                    raise ValueError("invalid identity decision status")
                rows = connection.execute(
                    "SELECT * FROM identity_candidates "
                    "WHERE status=? ORDER BY created_at, candidate_id",
                    (status,),
                ).fetchall()
        return [self._record(row) for row in rows]

    def events(self, candidate_id: str) -> list[dict[str, Any]]:
        """Return immutable audit events in revision order."""
        import json

        with self.state.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM identity_decision_events WHERE candidate_id=? ORDER BY revision",
                (candidate_id,),
            ).fetchall()
        if not rows:
            self.get_candidate(candidate_id)
        return [
            {
                "revision": row["revision"],
                "decision": row["decision"],
                "actor": row["actor"],
                "reason": row["reason"],
                "evidence": json.loads(row["evidence_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def reopen(
        self, candidate_id: str, *, expected_revision: int, actor: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Move a rejected or conflict candidate back to pending for a fresh review."""
        return self.decide(
            candidate_id,
            expected_revision=expected_revision,
            decision="pending",
            actor=actor,
            reason=reason,
        )

    def refresh_review(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor: str,
        authority_digest: str,
        profile_revision: int,
    ) -> dict[str, Any]:
        """Explicit re-review changes connection evidence, never the identity or account."""
        _text(actor, "actor")
        if not re.fullmatch(r"[0-9a-f]{64}", authority_digest):
            raise ValueError("invalid authority digest")
        if type(profile_revision) is not int or profile_revision < 1:
            raise ValueError("invalid profile revision")
        with self.state.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM identity_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            if row["revision"] != expected_revision:
                raise IdentityDecisionStale("identity candidate revision is stale")
            if row["status"] not in {"rejected", "conflict"}:
                raise IdentityDecisionConflict("only unresolved decisions can be reviewed again")
            timestamp = now()
            evidence = canonical_json(
                {
                    "previous_authority_digest": row["authority_digest"],
                    "authority_digest": authority_digest,
                    "profile_revision": profile_revision,
                }
            )
            connection.execute(
                "UPDATE identity_candidates SET status='pending',revision=revision+1,"
                "authority_digest=?,profile_revision=?,updated_at=? WHERE candidate_id=?",
                (authority_digest, profile_revision, timestamp, candidate_id),
            )
            connection.execute(
                "INSERT INTO identity_decision_events VALUES (NULL,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    expected_revision + 1,
                    "pending",
                    actor,
                    "explicit-account-recheck",
                    evidence,
                    timestamp,
                ),
            )
            return self._record(
                connection.execute(
                    "SELECT * FROM identity_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
            )

    def decide(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        decision: Literal["pending", "rejected", "approved", "conflict"],
        actor: str,
        reason: str | None = None,
        binder: Callable[[dict[str, Any], sqlite3.Connection], Any] | None = None,
    ) -> dict[str, Any]:
        if (
            decision not in {"pending", "rejected", "approved", "conflict"}
            or type(expected_revision) is not int
            or expected_revision < 1
        ):
            raise ValueError("invalid decision or revision")
        _text(actor, "actor")
        reason = _text(reason, "reason", optional=True)
        with self.state.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM identity_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown identity candidate: {candidate_id}")
            if row["revision"] != expected_revision:
                raise IdentityDecisionStale("identity candidate revision is stale")
            if decision == "pending":
                if row["status"] not in {"rejected", "conflict"}:
                    raise IdentityDecisionConflict(
                        "only rejected or conflict candidates can be reopened"
                    )
            elif row["status"] != "pending":
                raise IdentityDecisionConflict("identity candidate has already been decided")
            record = self._record(row)
            final = decision
            evidence: dict[str, Any] = {"candidate": candidate_id}
            if decision == "approved" and binder is None:
                raise ValueError("approved decisions require a binding callback")
            if decision == "approved" and binder is not None:
                connection.execute("SAVEPOINT identity_binder")
                try:
                    binder(record, connection)
                except BindingConflict as error:
                    connection.execute("ROLLBACK TO identity_binder")
                    connection.execute("RELEASE identity_binder")
                    final = "conflict"
                    reason = reason or "binding conflict"
                    evidence["error_type"] = type(error).__name__
                else:
                    connection.execute("RELEASE identity_binder")
            timestamp = now()
            connection.execute(
                "UPDATE identity_candidates SET status=?, revision=revision+1, updated_at=? "
                "WHERE candidate_id=? AND revision=?",
                (final, timestamp, candidate_id, expected_revision),
            )
            connection.execute(
                "INSERT INTO identity_decision_events VALUES (NULL,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    expected_revision + 1,
                    final,
                    actor,
                    reason,
                    canonical_json(evidence),
                    timestamp,
                ),
            )
            return self._record(
                connection.execute(
                    "SELECT * FROM identity_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
            )

    def mark_conflict(
        self, candidate_id: str, *, expected_revision: int, actor: str, reason_code: str
    ) -> dict[str, Any]:
        """Persist a bounded live-check conflict without attempting a binding."""
        allowed = {
            "target_changed",
            "account_not_found",
            "admin_account",
            "profile_stale",
            "binding_conflict",
        }
        if reason_code not in allowed:
            raise ValueError("unsupported identity conflict reason")
        return self.decide(
            candidate_id,
            expected_revision=expected_revision,
            decision="conflict",
            actor=actor,
            reason=reason_code,
        )


__all__ = [
    "IdentityDecisionConflict",
    "IdentityDecisionError",
    "IdentityDecisionService",
    "IdentityDecisionStale",
]
