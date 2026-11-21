"""Fail closed when a locally approved native account changes authentication identity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from gitferry.connection_probe import LocalReferences
from gitferry.directory_claims import DirectoryClaimsError, DirectoryClaimsUnavailable
from gitferry.directory_identity import (
    DirectorySelection,
    observe_target_directory,
    require_directory_revision,
    same_directory_identity,
)
from gitferry.directory_settings import DirectorySettingsConflict
from gitferry.state_codec import canonical_json, now
from gitferry.state_store import (
    BindingConflict,
    IdentityBindingRecord,
    OperationBlocked,
    StateStore,
)


def require_current_account(
    state: StateStore,
    binding: IdentityBindingRecord,
    account: Mapping[str, Any],
    references: LocalReferences | None = None,
) -> None:
    evidence = {
        "source_id": str(account["source_id"]) if account.get("source_id") is not None else None,
        "login_name": account.get("login_name"),
    }
    candidate_query = (
        "SELECT * FROM identity_candidates WHERE realm_key=? AND source_login=? "
        "AND source_external_id=? AND source_ou_path=? AND target_instance_key=? "
        "AND target_account_id=?"
    )
    candidate_key = (
        binding.source_realm_key,
        binding.source_login,
        binding.source_external_id,
        binding.source_ou_path,
        binding.target_instance_key,
        binding.target_account_id,
    )
    with state.read_connection() as connection:
        before = connection.execute(candidate_query, candidate_key).fetchone()
    directory_observed = None
    directory_invalid = False
    if before is not None:
        previous = json.loads(before["target_account_evidence"]).get("directory")
        if previous is not None:
            try:
                # Remote directory reads must not hold a SQLite write transaction.
                directory_observed = observe_target_directory(
                    state,
                    binding.target_instance_key,
                    DirectorySelection.model_validate(previous["selection"]),
                    evidence,
                    references,
                )
                directory_invalid = not same_directory_identity(previous, directory_observed)
            except DirectoryClaimsUnavailable:
                raise OperationBlocked(
                    "directory unavailable; no mapped write is allowed"
                ) from None
            except (DirectoryClaimsError, DirectorySettingsConflict, ValueError, KeyError):
                directory_invalid = True
    blocked = False
    with state.transaction() as connection:
        row = connection.execute(candidate_query, candidate_key).fetchone()
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
            if expected.get("directory") is not None:
                try:
                    require_directory_revision(
                        connection, binding.target_instance_key, expected["directory"]
                    )
                except BindingConflict:
                    directory_invalid = True
            invalid = (
                row["status"] != "approved"
                or any(expected.get(key) != evidence[key] for key in evidence)
                or directory_invalid
                or (expected.get("directory") is not None and directory_observed is None)
                or (before is not None and before["revision"] != row["revision"])
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
                            "directory-claim-changed"
                            if directory_invalid
                            else "native-authentication-changed",
                            canonical_json(
                                {
                                    "changed_fields": (["directory"] if directory_invalid else [])
                                    + [
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
