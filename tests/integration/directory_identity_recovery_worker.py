"""Fresh-process target worker for approved directory identity recovery checks.

The worker reads the existing package-admission state and performs only the
read/check path. A changed directory claim may durably mark the existing
candidate as conflict; it never creates or redirects a binding.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import plan_worker
from gitferry.connection_probe import LocalReferences
from gitferry.directory_claims import DirectoryClaimsUnavailable
from gitferry.directory_identity import DirectorySelection, observe_target_directory
from gitferry.identity_observations import require_current_account
from gitferry.state_store import OperationBlocked, StateStore
from gitferry.target_accounts import check_target_account

ROOT = Path(os.environ.get("GITFERRY_RECOVERY_ROOT", "/state/package-admission"))
DATABASE = ROOT / "state" / "state.sqlite3"
URL = os.environ.get("GITFERRY_RECOVERY_TARGET_URL", "http://target:3000")
TARGET = os.environ.get("GITFERRY_RECOVERY_TARGET", "target")


def _candidate(state: StateStore, login: str) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    with state.read_connection() as connection:
        rows = connection.execute(
            "SELECT b.binding_id, c.* FROM identity_bindings b "
            "JOIN identity_candidates c ON c.realm_key=b.source_realm_key "
            "AND c.source_login=b.source_login AND c.source_external_id=b.source_external_id "
            "AND c.source_ou_path=b.source_ou_path AND c.target_instance_key=b.target_instance_key "
            "AND c.target_account_id=b.target_account_id "
            "WHERE b.target_instance_key=? ORDER BY c.updated_at, c.candidate_id",
            (TARGET,),
        ).fetchall()
    ordered = sorted(
        rows,
        key=lambda row: (
            row["target_login"] != login,
            row["updated_at"],
            row["candidate_id"],
        ),
    )
    for row in ordered:
        candidate = dict(row)
        evidence = json.loads(candidate["target_account_evidence"])
        if candidate["target_login"] == login and evidence.get("directory") is not None:
            return state.get_identity_binding(row["binding_id"]), candidate, evidence
    raise RuntimeError("no approved directory-backed identity binding in package-admission state")


def _record(state: StateStore, candidate_id: str) -> dict[str, Any]:
    with state.read_connection() as connection:
        row = connection.execute(
            "SELECT status,revision,target_account_evidence FROM identity_candidates "
            "WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("approved candidate disappeared")
    return {
        "status": row["status"],
        "revision": row["revision"],
        "target_account_evidence": json.loads(row["target_account_evidence"]),
    }


def _binding_dict(binding: Any) -> dict[str, str]:
    return {
        "binding_id": binding.binding_id,
        "source_realm_key": binding.source_realm_key,
        "source_login": binding.source_login,
        "source_external_id": binding.source_external_id,
        "source_ou_path": binding.source_ou_path,
        "target_instance_key": binding.target_instance_key,
        "target_account_id": str(binding.target_account_id),
        "target_login": binding.target_login,
    }


def _account(
    state: StateStore, candidate: dict[str, Any], references: LocalReferences
) -> dict[str, Any]:
    plan_worker.ROOT = ROOT
    plan_worker.URL = URL
    with plan_worker.proxy():
        return check_target_account(
            state,
            target_instance_key=candidate["target_instance_key"],
            profile_revision=candidate["profile_revision"],
            target_login=candidate["target_login"],
            credential_ref=candidate["target_login"],
            references=references,
        )


def _directory_observation(
    state: StateStore,
    binding: Any,
    evidence: dict[str, Any],
    account: dict[str, Any],
    references: LocalReferences,
) -> dict[str, Any] | None:
    try:
        return observe_target_directory(
            state,
            binding.target_instance_key,
            DirectorySelection.model_validate(evidence["directory"]["selection"]),
            account["account_evidence"],
            references,
        )
    except Exception:
        return None


def run(mode: str, login: str = "ldap-reader") -> dict[str, Any]:
    state = StateStore(DATABASE)
    binding, candidate, evidence = _candidate(state, login)
    before_binding = _binding_dict(binding)
    references = LocalReferences(ROOT / "secrets", ROOT)
    if mode == "unavailable-ca":
        missing = ROOT / "recovery-missing-ca"
        missing.mkdir(mode=0o700, exist_ok=True)
        references = LocalReferences(ROOT / "secrets", missing)
    expected_claim = evidence["directory"].get("claim", {})
    result: dict[str, Any] = {
        "mode": mode,
        "candidate_id": candidate["candidate_id"],
        "before_status": candidate["status"],
        "expected_external_id": expected_claim.get("external_id"),
        "expected_groups": expected_claim.get("groups", []),
        "directory": evidence["directory"],
        "binding_before": before_binding,
    }
    if mode == "unavailable-ca":
        try:
            observe_target_directory(
                state,
                binding.target_instance_key,
                DirectorySelection.model_validate(evidence["directory"]["selection"]),
                {key: evidence[key] for key in ("source_id", "login_name")},
                references,
            )
        except DirectoryClaimsUnavailable as error:
            result["status"] = "blocked"
            result["blocked"] = True
            result["reason"] = str(error)
        else:
            result["status"] = "failed"
            result["blocked"] = False
            result["reason"] = "missing CA unexpectedly allowed directory observation"
        after = _record(state, candidate["candidate_id"])
        result["after_status"] = after["status"]
        result["after_revision"] = after["revision"]
        result["binding_after"] = _binding_dict(state.get_identity_binding(binding.binding_id))
        result["binding_unchanged"] = before_binding == result["binding_after"]
        result["changed_external_id"] = False
        return result
    try:
        account = _account(state, candidate, references)
        observed = _directory_observation(state, binding, evidence, account, references)
        if observed is not None:
            result["observed_external_id"] = observed["claim"].get("external_id")
            result["observed_groups"] = observed["claim"].get("groups", [])
        require_current_account(state, binding, account["account_evidence"], references)
        result["status"] = "passed"
        result["blocked"] = False
    except OperationBlocked as error:
        result["status"] = "blocked"
        result["blocked"] = True
        result["reason"] = str(error)
    except Exception as error:  # bounded probe output; no remote bodies or secrets
        result["status"] = "failed"
        result["blocked"] = False
        result["error_type"] = type(error).__name__
        result["reason"] = "unexpected recovery probe failure"
    after = _record(state, candidate["candidate_id"])
    result["after_status"] = after["status"]
    result["after_revision"] = after["revision"]
    result["binding_after"] = _binding_dict(state.get_identity_binding(binding.binding_id))
    result["binding_unchanged"] = before_binding == result["binding_after"]
    result["changed_external_id"] = result.get("observed_external_id") not in (
        None,
        result["expected_external_id"],
    )
    result["changed_groups"] = (
        "observed_groups" in result and result["observed_groups"] != result["expected_groups"]
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("verify", "unavailable-ca"))
    parser.add_argument("--login", default="ldap-reader")
    args = parser.parse_args()
    print(json.dumps(run(args.mode, args.login), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "reason": "unexpected recovery probe failure",
                }
            )
        )
        raise SystemExit(1) from None
