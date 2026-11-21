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


@pytest.mark.parametrize("decision", ["approved", "pending"])
def test_missing_review_credential_is_input_error_not_identity_conflict(
    tmp_path, monkeypatch, decision
):
    app, request, _, _ = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request)
    if decision == "pending":
        candidate = app.decisions.mark_conflict(
            candidate["candidate_id"],
            expected_revision=1,
            actor="operator",
            reason_code="target_changed",
        )
    events = app.decisions.events(candidate["candidate_id"])
    with pytest.raises(ValueError, match="credential reference is required"):
        app.decide(
            candidate["candidate_id"],
            IdentityAction(
                expected_revision=candidate["revision"], decision=decision, profile_revision=1
            ),
            actor="operator",
        )
    assert app.decisions.get_candidate(candidate["candidate_id"]) == candidate
    assert app.decisions.events(candidate["candidate_id"]) == events


def test_directory_membership_uses_explicit_local_reference_roots(tmp_path, monkeypatch):
    from gitferry.operations import OperationExecutor
    from gitferry.provisioning import (
        ProvisioningMember,
        ProvisioningPlan,
        ProvisioningTeam,
        preflight_provisioning_operations,
        provision_selected_repository,
    )
    from gitferry.state_store import OriginKey
    from test_provisioning import FakeProvisioningClient

    app, request, account, _ = directory_candidate(tmp_path, monkeypatch)
    approve(app, app.propose(request))
    approved = binding(app)
    organization = app.state.register_origin(OriginKey("a", "access-lab", "organization", "910"))
    repository = app.state.register_origin(
        OriginKey("a", "access-lab/selected-private", "repository", "920")
    )
    team = app.state.register_origin(OriginKey("a", "access-lab", "team", "930"))
    plan = ProvisioningPlan(
        target_instance_key="b",
        organization_origin_id=organization.id,
        organization="access-lab",
        repository_origin_id=repository.id,
        repository="access-lab/selected-private",
        teams=(
            ProvisioningTeam(
                team.id, "readers", "read", (ProvisioningMember(approved.binding_id, "read"),)
            ),
        ),
    )
    client = FakeProvisioningClient()
    client.users[approved.target_login] = account
    preflight_provisioning_operations(app.state, client, plan, references=app.references)
    with OperationExecutor(app.state, "directory-membership") as executor:
        result = provision_selected_repository(
            app.state, executor, client, plan, references=app.references
        )
    assert all(operation.status == "confirmed" for operation in result.operations)
    assert client.memberships == 1
    assert app.decisions.list_candidates()[0]["status"] == "approved"


@pytest.mark.parametrize("stage", ["proposal", "approval", "execution"])
def test_directory_ca_content_rotation_requires_explicit_review(tmp_path, monkeypatch, stage):
    app, request, account, _ = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request) if stage != "proposal" else None
    if stage == "execution":
        approve(app, candidate)
    (app.references.cas / "directory.pem").write_text("another synthetic CA, same reference")
    if stage == "proposal":
        with pytest.raises(BindingConflict, match="changed since review"):
            app.propose(request)
        assert app.decisions.list_candidates() == []
    elif stage == "approval":
        assert approve(app, candidate)["status"] == "conflict"
    else:
        with pytest.raises(OperationBlocked, match="authentication changed"):
            require_current_account(app.state, binding(app), account, app.references)
        assert app.decisions.get_candidate(candidate["candidate_id"])["status"] == "conflict"


@pytest.mark.parametrize("rotation", ["ca", "configuration"])
@pytest.mark.parametrize("already_bound", [False, True])
def test_explicit_directory_trust_rereview_preserves_subject_and_binding(
    tmp_path, monkeypatch, rotation, already_bound
):
    app, request, account, _ = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request)
    previous_binding = None
    if already_bound:
        candidate = approve(app, candidate)
        previous_binding = binding(app)
    original_directory = candidate["account_evidence"]["directory"]
    if rotation == "ca":
        (app.references.cas / "directory.pem").write_text("rotated synthetic trust bundle")
    else:
        settings = DirectorySettingsService(app.state)
        source = settings.get("b", "directory-b")["source"]
        source["userFilter"] = "(&(objectClass=person)(uid=%s))"
        settings.save("b", source, 1)
    if already_bound:
        with pytest.raises(OperationBlocked):
            require_current_account(app.state, previous_binding, account, app.references)
        candidate = app.decisions.get_candidate(candidate["candidate_id"])
    else:
        candidate = approve(app, candidate)
    assert candidate["status"] == "conflict"
    candidate = app.decide(
        candidate["candidate_id"],
        IdentityAction(
            expected_revision=candidate["revision"],
            decision="pending",
            profile_revision=1,
            credential_ref="author",
        ),
        actor="trust-reviewer",
    )
    assert candidate["status"] == "pending"
    assert candidate["account_evidence"]["directory"]["claim"] == original_directory["claim"]
    event = app.decisions.events(candidate["candidate_id"])[-1]
    assert event["actor"] == "trust-reviewer"
    assert event["evidence"]["previous_directory_digest"] != event["evidence"]["directory_digest"]
    if already_bound:
        assert binding(app) == previous_binding
    else:
        with app.state.read_connection() as connection:
            assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0
    candidate = app.decide(
        candidate["candidate_id"],
        IdentityAction(
            expected_revision=candidate["revision"],
            decision="approved",
            profile_revision=1,
            credential_ref="author",
        ),
        actor="trust-reviewer",
    )
    assert candidate["status"] == "approved"
    require_current_account(app.state, binding(app), account, app.references)
    if already_bound:
        assert binding(app) == previous_binding


@pytest.mark.parametrize(
    "changed",
    [
        {"external_id": "replacement-person"},
        {"dn": "uid=replacement,ou=Other"},
        {"groups": ("another-group",)},
    ],
)
def test_trust_rereview_cannot_change_directory_subject(tmp_path, monkeypatch, changed):
    app, request, account, current = directory_candidate(tmp_path, monkeypatch)
    candidate = approve(app, app.propose(request))
    original = binding(app)
    current[0] = replace(current[0], **changed)
    with pytest.raises(OperationBlocked):
        require_current_account(app.state, original, account, app.references)
    candidate = app.decisions.get_candidate(candidate["candidate_id"])
    with pytest.raises(BindingConflict, match="cannot reopen"):
        app.decide(
            candidate["candidate_id"],
            IdentityAction(
                expected_revision=candidate["revision"],
                decision="pending",
                profile_revision=1,
                credential_ref="author",
            ),
            actor="operator",
        )
    assert app.decisions.get_candidate(candidate["candidate_id"]) == candidate
    assert binding(app) == original


def test_directory_reopen_cannot_commit_a_configuration_changed_during_recheck(
    tmp_path, monkeypatch
):
    app, request, _, _ = directory_candidate(tmp_path, monkeypatch)
    candidate = app.propose(request)
    candidate = app.decisions.mark_conflict(
        candidate["candidate_id"],
        expected_revision=1,
        actor="operator",
        reason_code="target_changed",
    )
    original = app.decisions.refresh_review

    def rotate_before_commit(*args, **kwargs):
        settings = DirectorySettingsService(app.state)
        source = settings.get("b", "directory-b")["source"]
        source["userFilter"] = "(&(uid=%s)(objectClass=person))"
        settings.save("b", source, 1)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.decisions, "refresh_review", rotate_before_commit)
    with pytest.raises(BindingConflict, match="configuration changed"):
        app.decide(
            candidate["candidate_id"],
            IdentityAction(
                expected_revision=candidate["revision"],
                decision="pending",
                profile_revision=1,
                credential_ref="author",
            ),
            actor="operator",
        )
    assert app.decisions.get_candidate(candidate["candidate_id"]) == candidate
