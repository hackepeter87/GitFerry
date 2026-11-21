"""Directory facts are target evidence, never source identity or automatic authority."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from gitferry.connection_probe import LocalReferences
from gitferry.directory_claims import DirectoryClaimSnapshot, DirectoryClaimsUnavailable
from gitferry.directory_identity import DirectorySelection
from gitferry.directory_settings import DirectorySettingsService
from gitferry.identity_application import CheckAccountRequest, IdentityAction
from gitferry.identity_observations import require_current_account
from gitferry.state_store import BindingConflict, OperationBlocked
from test_identity_application import prepared
from test_package_decisions import remote


def directory_candidate(tmp_path, monkeypatch):
    app, request, _ = prepared(tmp_path, monkeypatch)
    realm = "target/directory"
    app.state.register_realm(realm, "Target directory", "distinct-target-marker")
    source = {
        "name": "directory-b",
        "type": "ldaps",
        "mode": "simple",
        "directoryRealm": realm,
        "host": "directory.invalid",
        "userDn": "uid=%s,ou=Target,dc=example,dc=invalid",
        "userFilter": "(uid=%s)",
        "emailAttribute": "mail",
        "caSecretRef": "directory.pem",
        "claimLookup": {
            "searchBase": "ou=Target,dc=example,dc=invalid",
            "loginAttribute": "uid",
            "externalIdAttribute": "employeeNumber",
        },
    }
    DirectorySettingsService(app.state).save("b", source, None)
    cas = tmp_path / "cas"
    cas.mkdir()
    (cas / "directory.pem").write_text("synthetic CA passed to injected read-only transport")
    app.references = LocalReferences(secrets=app.references.secrets, cas=cas)
    account = {
        "id": int(request.target_account_id),
        "login": request.target_login,
        "is_admin": False,
        "source_id": 7,
        "login_name": "ldap.user",
    }
    remote(monkeypatch, account)
    claim = DirectoryClaimSnapshot(
        realm,
        "ldap.user",
        "TARGET-ID",
        "uid=ldap.user,ou=Target,dc=example,dc=invalid",
        "ou=Target,dc=example,dc=invalid",
        None,
        ("cn=readers,dc=example,dc=invalid",),
    )
    current = [claim]
    monkeypatch.setattr("gitferry.directory_identity.observe_claim", lambda *a, **kw: current[0])
    selection = DirectorySelection(
        source_name="directory-b", source_revision=1, native_source_id="7", login="ldap.user"
    )
    checked = app.check_account(
        CheckAccountRequest(
            target_instance_key="b",
            profile_revision=1,
            target_login=request.target_login,
            credential_ref="author",
            directory=selection,
        )
    )
    request = request.model_copy(
        update={
            "credential_ref": "author",
            "directory": selection,
            "directory_digest": checked["directory_digest"],
        }
    )
    return app, request, account, current


def approve(app, candidate):
    return app.decide(
        candidate["candidate_id"],
        IdentityAction(
            expected_revision=1,
            decision="approved",
            profile_revision=1,
            credential_ref="author",
        ),
        actor="operator",
    )


def binding(app):
    with app.state.read_connection() as connection:
        identifier = connection.execute("SELECT binding_id FROM identity_bindings").fetchone()[0]
    return app.state.get_identity_binding(identifier)


def test_distinct_source_target_directory_identity_missing_email_and_live_check(
    tmp_path, monkeypatch
):
    app, request, account, current = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request)
    assert candidate["selector"]["external_id"] != "TARGET-ID"
    assert candidate["selector"]["realm_key"] != "target/directory"
    assert candidate["account_evidence"]["directory"]["claim"]["email"] is None
    assert approve(app, candidate)["status"] == "approved"
    before = binding(app)
    require_current_account(app.state, before, account, app.references)
    current[0] = replace(current[0], email="optional@example.invalid")
    require_current_account(app.state, before, account, app.references)
    assert binding(app) == before


@pytest.mark.parametrize("change", ["external_id", "ou_path", "groups"])
def test_changed_target_claim_blocks_write_persistently_without_rebinding(
    tmp_path, monkeypatch, change
):
    app, request, account, current = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request)
    approve(app, candidate)
    before = binding(app)
    original = current[0]
    current[0] = replace(original, **{change: ("different",) if change == "groups" else "changed"})
    with pytest.raises(OperationBlocked):
        require_current_account(app.state, before, account, app.references)
    current[0] = original
    with pytest.raises(OperationBlocked):
        require_current_account(app.state, before, account, app.references)
    assert binding(app) == before
    assert app.decisions.get_candidate(candidate["candidate_id"])["status"] == "conflict"
    assert (
        app.decisions.events(candidate["candidate_id"])[-1]["reason"] == "directory-claim-changed"
    )


def test_unavailable_directory_blocks_without_mutating_confirmed_binding(tmp_path, monkeypatch):
    app, request, account, _ = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request)
    approve(app, candidate)
    before = binding(app)

    def unavailable(*args, **kwargs):
        raise DirectoryClaimsUnavailable("directory unavailable")

    monkeypatch.setattr("gitferry.directory_identity.observe_claim", unavailable)
    with pytest.raises(OperationBlocked, match="unavailable"):
        require_current_account(app.state, before, account, app.references)
    assert binding(app) == before
    assert app.decisions.get_candidate(candidate["candidate_id"])["status"] == "approved"


def test_directory_selection_needs_own_credentials_and_checked_digest(tmp_path, monkeypatch):
    app, request, _, _ = directory_candidate(tmp_path, monkeypatch)
    with pytest.raises(BindingConflict):
        app.propose(request.model_copy(update={"credential_ref": None}))
    with pytest.raises(BindingConflict):
        app.propose(request.model_copy(update={"directory_digest": "0" * 64}))
    assert app.decisions.list_candidates() == []
    candidate = app.propose(request)
    assert "synthetic-author-credential" not in json.dumps(candidate)
    assert "credential_ref" not in json.dumps(candidate)


def test_observed_native_source_is_proposed_but_never_implicitly_approved(tmp_path, monkeypatch):
    app, request, _, _ = directory_candidate(tmp_path, monkeypatch)
    request = request.model_copy(
        update={"directory": request.directory.model_copy(update={"native_source_id": None})}
    )
    candidate = app.propose(request)
    assert candidate["account_evidence"]["directory"]["selection"]["native_source_id"] == "7"
    assert candidate["status"] == "pending"
    with app.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,value", [("source_id", 0), ("source_id", 8), ("login_name", "another.directory.user")]
)
def test_wrong_native_directory_relation_prevents_candidate(tmp_path, monkeypatch, field, value):
    from gitferry.directory_claims import DirectoryClaimsConflict

    app, request, account, _ = directory_candidate(tmp_path, monkeypatch)
    remote(monkeypatch, {**account, field: value})
    with pytest.raises(DirectoryClaimsConflict):
        app.propose(request)
    assert app.decisions.list_candidates() == []


def test_configuration_rotation_between_observation_and_approval_conflicts(tmp_path, monkeypatch):
    app, request, _, _ = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request)
    original = app.decisions.decide

    def changed_before_commit(*args, **kwargs):
        settings = DirectorySettingsService(app.state)
        source = settings.get("b", "directory-b")["source"]
        source["claimLookup"]["externalIdAttribute"] = "differentId"
        settings.save("b", source, 1)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.decisions, "decide", changed_before_commit)
    assert approve(app, candidate)["status"] == "conflict"
    with app.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0
