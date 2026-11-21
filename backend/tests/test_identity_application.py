"""Application boundaries for local identity approval, without remote writes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.identity_application import CandidateRequest, IdentityAction, IdentityApplication
from gitferry.package_decisions import AccountRequest
from gitferry.state_store import BindingConflict, StateStore
from test_package_decisions import approve_realm, remote, setup


def prepared(tmp_path, monkeypatch):
    packages, ref, initial, intent = setup(tmp_path, monkeypatch)
    approve_realm(packages, ref, initial, intent)
    remote(monkeypatch)
    app = IdentityApplication(packages.state, packages.references)
    selector = initial["identities"][0]["selector"]
    checked = packages.check_account(
        ref,
        AccountRequest(**intent, target_login="target.author"),
    )
    request = CandidateRequest(
        target_instance_key="b",
        profile_revision=1,
        authority_digest=checked["authority_digest"],
        selector=selector,
        target_account_id=checked["target_account_id"],
        target_login="target.author",
        prerequisites={"issue_author": True, "comment_author": False, "team_member": True},
    )
    return app, request, packages


def test_explicit_decision_reuses_binding_without_rights_or_remote_write(tmp_path, monkeypatch):
    app, request, packages = prepared(tmp_path, monkeypatch)
    candidate = app.propose(request)
    assert candidate["status"] == "pending"
    with app.state.read_connection() as c:
        assert c.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0
    approved = app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=1, decision="approved", profile_revision=1),
        actor="admin-actor",
    )
    assert approved["status"] == "approved"
    restarted = IdentityApplication(StateStore(app.state.path), packages.references)
    assert restarted.decisions.get_candidate(candidate["candidate_id"]) == approved
    with app.state.read_connection() as c:
        assert c.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
    assert app.decisions.events(candidate["candidate_id"])[-1]["actor"] == "admin-actor"


def test_replaced_account_between_review_and_approval_is_visible_conflict(tmp_path, monkeypatch):
    app, request, _ = prepared(tmp_path, monkeypatch)
    candidate = app.propose(request)
    remote(monkeypatch, {"id": 42, "login": "target.author", "is_admin": False})
    result = app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=1, decision="approved", profile_revision=1),
        actor="operator",
    )
    assert result["status"] == "conflict"
    with app.state.read_connection() as c:
        assert c.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0


def test_candidate_cannot_claim_another_reviewed_account(tmp_path, monkeypatch):
    app, request, _ = prepared(tmp_path, monkeypatch)
    with pytest.raises(BindingConflict):
        app.propose(request.model_copy(update={"target_account_id": "41"}))
    assert app.decisions.list_candidates() == []


def test_api_auth_csrf_local_actor_and_large_ids(tmp_path, monkeypatch):
    app, request, packages = prepared(tmp_path, monkeypatch)
    monkeypatch.setenv("GITFERRY_SECRET_DIR", str(packages.references.secrets))
    # Inject the established test-only reference resolver, never a browser-provided path.
    monkeypatch.setattr("gitferry.identity_api.IdentityApplication", lambda state: app)
    auth = AuthService(app.state)
    auth.bootstrap_admin("operator", "synthetic-long-admin-password")
    web = create_app(auth_service=auth)
    web.state.runtime = SimpleNamespace(store=app.state, paths=packages.admissions.paths)
    with TestClient(web, base_url="https://testserver") as client:
        assert client.get("/api/v1/identities").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "operator", "password": "synthetic-long-admin-password"},
            ).status_code
            == 200
        )
        assert (
            client.post("/api/v1/identities/candidates", json=request.model_dump()).status_code
            == 403
        )
        headers = {"x-gitferry-csrf": client.cookies["gitferry_csrf"]}
        response = client.post(
            "/api/v1/identities/candidates", json=request.model_dump(), headers=headers
        )
        assert response.status_code == 200, response.text
        candidate = response.json()
        assert candidate["target"]["account_id"] == "9007199254740993"
        response = client.post(
            "/api/v1/identities/" + candidate["candidate_id"] + "/decision",
            json={"expected_revision": 1, "decision": "approved", "profile_revision": 1},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        events = client.get("/api/v1/identities/" + candidate["candidate_id"] + "/events").json()
        assert (
            events[-1]["actor"] != "operator"
        )  # immutable local account ID, not browser-supplied name
        assert "synthetic-long-admin-password" not in json.dumps(events)


@pytest.mark.parametrize(
    "account",
    [
        {"id": 9007199254740993, "login": "target.author", "is_admin": True},
        {"id": 9007199254740993, "login": "renamed.author", "is_admin": False},
    ],
)
def test_invalid_live_account_is_durable_conflict_not_fallback(tmp_path, monkeypatch, account):
    app, request, _ = prepared(tmp_path, monkeypatch)
    candidate = app.propose(request)
    remote(monkeypatch, account)
    result = app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=1, decision="approved", profile_revision=1),
        actor="operator",
    )
    assert result["status"] == "conflict"
    assert app.decisions.events(candidate["candidate_id"])[-1]["reason"] == "target_changed"
    with app.state.read_connection() as c:
        assert c.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0


def test_package_confirmation_cannot_bypass_rejected_identity(tmp_path, monkeypatch):
    from gitferry.package_decisions import BindingRequest, TargetRequest

    app, request, packages = prepared(tmp_path, monkeypatch)
    candidate = app.propose(request)
    app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=1, decision="rejected", profile_revision=1),
        actor="operator",
    )
    initial = packages.prerequisites(
        "received", TargetRequest(target_instance_key="b", profile_revision=1)
    )
    with pytest.raises(BindingConflict):
        packages.confirm_binding(
            "received",
            BindingRequest(
                target_instance_key="b",
                profile_revision=1,
                expected_digest=initial["digest_sha256"],
                selector=request.selector,
                target_account_id=request.target_account_id,
                target_login=request.target_login,
            ),
        )
    assert app.decisions.get_candidate(candidate["candidate_id"])["status"] == "rejected"


def test_reopen_rechecks_account_before_explicit_late_approval(tmp_path, monkeypatch):
    app, request, _ = prepared(tmp_path, monkeypatch)
    candidate = app.propose(request)
    app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=1, decision="rejected", profile_revision=1),
        actor="operator",
    )
    reopened = app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=2, decision="pending", profile_revision=1),
        actor="operator",
    )
    assert reopened["status"] == "pending"
    assert (
        app.decisions.events(candidate["candidate_id"])[-1]["reason"] == "explicit-account-recheck"
    )
    approved = app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=3, decision="approved", profile_revision=1),
        actor="operator",
    )
    assert approved["status"] == "approved"
    assert len(app.decisions.events(candidate["candidate_id"])) == 4


def test_cli_uses_same_decisions_and_rejects_embedded_approval(tmp_path, monkeypatch, capsys):
    from gitferry import identity_cli
    from gitferry.cli import build_parser

    app, request, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(identity_cli, "IdentityApplication", lambda state: app)
    document = tmp_path / "candidate.json"
    document.write_text(request.model_dump_json())
    args = build_parser().parse_args(
        ["identities", "--database", str(app.state.path), "propose", "--input", str(document)]
    )
    assert identity_cli.execute(args) == 0
    candidate = json.loads(capsys.readouterr().out)
    document.write_text(
        json.dumps({"expected_revision": 1, "decision": "approved", "profile_revision": 1})
    )
    args = build_parser().parse_args(
        [
            "identities",
            "--database",
            str(app.state.path),
            "decide",
            "--candidate-id",
            candidate["candidate_id"],
            "--input",
            str(document),
        ]
    )
    assert identity_cli.execute(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "approved"
    assert app.decisions.events(candidate["candidate_id"])[-1]["actor"] == "local-cli"
    document.write_text(json.dumps({**request.model_dump(), "approved": True}))
    args = build_parser().parse_args(
        ["identities", "--database", str(app.state.path), "propose", "--input", str(document)]
    )
    assert identity_cli.execute(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


@pytest.mark.parametrize(
    "changed",
    [
        {"source_id": 8, "login_name": "same-user"},
        {"source_id": 7, "login_name": "replacement-user"},
        {"source_id": 7, "login_name": "same-user", "prohibit_login": True},
    ],
)
def test_native_authentication_source_change_blocks_approval(tmp_path, monkeypatch, changed):
    app, request, _ = prepared(tmp_path, monkeypatch)
    remote(
        monkeypatch,
        {
            "id": 9007199254740993,
            "login": "target.author",
            "is_admin": False,
            "source_id": 7,
            "login_name": "same-user",
            "prohibit_login": False,
        },
    )
    candidate = app.propose(request)
    assert candidate["account_evidence"] == {"source_id": "7", "login_name": "same-user"}
    remote(
        monkeypatch,
        {"id": 9007199254740993, "login": "target.author", "is_admin": False, **changed},
    )
    result = app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=1, decision="approved", profile_revision=1),
        actor="operator",
    )
    assert result["status"] == "conflict"
    with app.state.read_connection() as c:
        assert c.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0


def test_preflight_authentication_drift_remains_blocked_when_reopened(tmp_path, monkeypatch):
    from gitferry.identity_observations import require_current_account
    from gitferry.state_store import OperationBlocked

    app, request, _ = prepared(tmp_path, monkeypatch)
    account = {
        "id": 9007199254740993,
        "login": "target.author",
        "is_admin": False,
        "source_id": 7,
        "login_name": "directory-user",
        "prohibit_login": False,
    }
    remote(monkeypatch, account)
    candidate = app.propose(request)
    app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=1, decision="approved", profile_revision=1),
        actor="operator",
    )
    binding = app.state.find_identity_binding(
        source_realm_key=request.selector.realm_key,
        source_login=request.selector.login,
        source_external_id=request.selector.external_id,
        source_ou_path=request.selector.ou_path,
        target_instance_key="b",
    )
    require_current_account(app.state, binding, account)
    with pytest.raises(OperationBlocked):
        require_current_account(app.state, binding, {**account, "source_id": 8})
    assert app.decisions.get_candidate(candidate["candidate_id"])["status"] == "conflict"
    # Even restoring the old native fact does not silently resolve a local conflict.
    with pytest.raises(OperationBlocked):
        require_current_account(app.state, binding, account)
    app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=3, decision="pending", profile_revision=1),
        actor="operator",
    )
    with pytest.raises(OperationBlocked):
        require_current_account(app.state, binding, account)
    app.decide(
        candidate["candidate_id"],
        IdentityAction(expected_revision=4, decision="approved", profile_revision=1),
        actor="operator",
    )
    require_current_account(app.state, binding, account)
