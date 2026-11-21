"""Fail closed when a locally approved native account changes authentication identity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from gitferry.state_codec import canonical_json, now
from gitferry.state_store import IdentityBindingRecord, OperationBlocked, StateStore


def require_current_account(
    state: StateStore, binding: IdentityBindingRecord, account: Mapping[str, Any]
) -> None:
    evidence = {
        "source_id": str(account["source_id"]) if account.get("source_id") is not None else None,
        "login_name": account.get("login_name"),
    }
    blocked = False
    with state.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM identity_candidates WHERE realm_key=? AND source_login=? "
            "AND source_external_id=? AND source_ou_path=? AND target_instance_key=? "
            "AND target_account_id=?",
            (
                binding.source_realm_key,
                binding.source_login,
                binding.source_external_id,
                binding.source_ou_path,
                binding.target_instance_key,
                binding.target_account_id,
            ),
        ).fetchone()
        # Existing legacy bindings stay valid; proposal history never invents a new approval.
        previously_approved = (
            row is not None
            and connection.execute(
                "SELECT 1 FROM identity_decision_events WHERE candidate_id=? "
                "AND decision='approved' LIMIT 1",
                (row["candidate_id"],),
            ).fetchone()
            is not None
        )
        if row is not None and (previously_approved or row["status"] == "conflict"):
            expected = json.loads(row["target_account_evidence"])
            invalid = (
                row["status"] != "approved"
                or expected != evidence
                or account.get("prohibit_login") is True
                or account.get("active") is False
            )
            if invalid:
                blocked = True
                if row["status"] == "approved":
                    timestamp = now()
                    connection.execute(
                        "UPDATE identity_candidates SET status='conflict',revision=revision+1,"
                        "updated_at=? "
                        "WHERE candidate_id=?",
                        (timestamp, row["candidate_id"]),
                    )
                    connection.execute(
                        "INSERT INTO identity_decision_events VALUES (NULL,?,?,?,?,?,?,?)",
                        (
                            row["candidate_id"],
                            row["revision"] + 1,
                            "conflict",
                            "target-preflight",
                            "native-authentication-changed",
                            canonical_json(
                                {
                                    "changed_fields": [
                                        key
                                        for key in evidence
                                        if expected.get(key) != evidence[key]
                                    ]
                                }
                            ),
                            timestamp,
                        ),
                    )
    if blocked:
        raise OperationBlocked(
            "mapped account authentication changed; explicit local review required"
        )
