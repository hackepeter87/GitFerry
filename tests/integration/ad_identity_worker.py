"""Target-local AD binding checks through the existing application services."""

from __future__ import annotations

import argparse
import json
from typing import Any

import directory_identity_recovery_worker as recovery
import httpx
import plan_worker
from gitferry.connection_profiles import ProfileService
from gitferry.directory_identity import DirectorySelection
from gitferry.directory_settings import DirectorySettingsService
from gitferry.identity_application import (
    CandidateRequest,
    CheckAccountRequest,
    IdentityAction,
    IdentityApplication,
)
from gitferry.state_store import BindingConflict, StateStore

LOGIN = "ad-local-alias"
CANDIDATE = recovery.ROOT / "ad-identity-candidate.json"


def application() -> IdentityApplication:
    return IdentityApplication(
        StateStore(recovery.DATABASE),
        recovery.LocalReferences(recovery.ROOT / "secrets", recovery.ROOT),
    )


def initialize(app: IdentityApplication) -> dict[str, Any]:
    # Local fixture credentials are prerequisites, never copied from exporter A.
    with httpx.Client(base_url=recovery.URL + "/api/v1", trust_env=False, timeout=15) as client:
        token = client.post(
            f"/users/{LOGIN}/tokens",
            auth=(LOGIN, "Synthetic-User-42!"),
            json={"name": "ad-identity-qualification", "scopes": ["read:user"]},
        )
        token.raise_for_status()
        path = recovery.ROOT / "secrets" / LOGIN
        path.write_text(token.json()["sha1"])
        path.chmod(0o600)
    bind_secret = recovery.ROOT / "secrets" / "ad-directory-bind"
    bind_secret.write_text("Synthetic-User-43!")
    bind_secret.chmod(0o600)
    app.state.register_realm("synthetic-source-ad", "Synthetic source claim", "synthetic-source-ad")
    app.state.register_realm("SYNTH-AD", "Synthetic Samba realm", "synthetic-samba-target")
    settings = DirectorySettingsService(app.state).save(
        "target",
        {
            "name": "synthetic-ad-simple",
            "type": "ldaps",
            "mode": "simple",
            "directoryRealm": "SYNTH-AD",
            "host": "synthdc",
            "port": 636,
            "userDn": "CN=%s,OU=People,DC=synth,DC=ad",
            "userFilter": "(&(sAMAccountName=%s)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
            "usernameAttribute": "sAMAccountName",
            "emailAttribute": "mail",
            "caSecretRef": "directory.pem",
            "claimLookup": {
                "searchBase": "OU=People,DC=synth,DC=ad",
                "loginAttribute": "sAMAccountName",
                "externalIdAttribute": "objectSid",
                "emailAttribute": "mail",
                "bindDn": "CN=ad-nomail,OU=People,DC=synth,DC=ad",
                "bindCredentialRef": "ad-directory-bind",
                "groupSearchBase": "OU=Groups,DC=synth,DC=ad",
                "groupMemberAttribute": "member",
            },
        },
        None,
    )
    checked_request = CheckAccountRequest(
        target_instance_key="target",
        profile_revision=ProfileService(app.state).get("target").revision,
        target_login=LOGIN,
        credential_ref=LOGIN,
        directory=DirectorySelection(
            source_name="synthetic-ad-simple",
            source_revision=settings["revision"],
            login="ldap-same",
        ),
    )
    checked = app.check_account(checked_request)
    claim = checked["directory_evidence"]["claim"]
    if claim["email"] is not None or claim["external_id_kind"] != "sid":
        raise RuntimeError("AD missing-mail SID fixture was not observed")
    candidate = app.propose(
        CandidateRequest(
            **checked_request.model_dump(),
            authority_digest=checked["authority_digest"],
            target_account_id=checked["target_account_id"],
            directory_digest=checked["directory_digest"],
            selector={
                "realm_key": "synthetic-source-ad",
                "login": "source-ad-author",
                "external_id": "source-ad-1",
                "ou_path": "/",
            },
            prerequisites={"issue_author": False, "comment_author": False, "team_member": True},
        )
    )
    CANDIDATE.write_text(json.dumps({"candidate_id": candidate["candidate_id"]}))
    approved = app.decide(
        candidate["candidate_id"],
        IdentityAction(
            expected_revision=candidate["revision"],
            decision="approved",
            profile_revision=checked_request.profile_revision,
            credential_ref=LOGIN,
        ),
        actor="synthetic-ad-operator",
    )
    if approved["status"] != "approved":
        raise RuntimeError("AD candidate was not explicitly approved")
    return {
        "status": "passed",
        "candidate_id": candidate["candidate_id"],
        "claim": claim,
        "native_account_id": checked["target_account_id"],
        "missing_mail_binding_approved": True,
    }


def reopen(app: IdentityApplication, *, denied: bool) -> dict[str, Any]:
    identifier = json.loads(CANDIDATE.read_text())["candidate_id"]
    candidate = app.decisions.get_candidate(identifier)
    if candidate["status"] != "conflict":
        raise RuntimeError("explicit AD review requires a durable conflict")
    action = IdentityAction(
        expected_revision=candidate["revision"],
        decision="pending",
        profile_revision=ProfileService(app.state).get("target").revision,
        credential_ref=LOGIN,
    )
    try:
        pending = app.decide(identifier, action, actor="synthetic-ad-operator")
    except BindingConflict:
        if not denied:
            raise
        if app.decisions.get_candidate(identifier)["status"] != "conflict":
            raise RuntimeError("failed AD review changed the conflict") from None
        return {"status": "blocked", "new_sid_cannot_reopen": True}
    if denied:
        raise RuntimeError("new AD SID silently replaced the reviewed identity")
    approved = app.decide(
        identifier,
        action.model_copy(
            update={"decision": "approved", "expected_revision": pending["revision"]}
        ),
        actor="synthetic-ad-operator",
    )
    if approved["status"] != "approved":
        raise RuntimeError("restored AD subject could not be explicitly approved")
    return {"status": "passed", "explicit_reapproval": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("initialize", "verify", "reopen", "reopen-denied"))
    mode = parser.parse_args().mode
    if mode == "verify":
        result = recovery.run("verify", LOGIN)
        native = httpx.get(
            recovery.URL + "/api/v1/user",
            auth=(LOGIN, "Synthetic-User-42!"),
            trust_env=False,
            timeout=15,
        )
        result["native_auth_status"] = native.status_code
        if native.status_code == 200:
            result["native_account_id"] = str(native.json()["id"])
            if result["native_account_id"] != result["binding_after"]["target_account_id"]:
                raise RuntimeError("native AD account was redirected")
            denied = httpx.get(
                recovery.URL + "/api/v1/repos/source-export-lab/selected",
                auth=(LOGIN, "Synthetic-User-42!"),
                trust_env=False,
                timeout=15,
            )
            if denied.status_code not in (403, 404):
                raise RuntimeError("binding approval alone granted unplanned repository access")
            result["no_implicit_repository_access"] = True
    else:
        plan_worker.ROOT, plan_worker.URL = recovery.ROOT, recovery.URL
        with plan_worker.proxy():
            app = application()
            result = (
                initialize(app)
                if mode == "initialize"
                else reopen(app, denied=mode == "reopen-denied")
            )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
