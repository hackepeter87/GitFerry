from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.connection_probe import ConnectionUnavailable, LocalReferences
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.package_admission import PackageAdmissionConflict
from gitferry.package_decisions import (
    AccountRequest,
    BindingRequest,
    PackageDecisions,
    RealmRequest,
    SenderRequest,
    TargetRequest,
)
from gitferry.state_store import BindingConflict, StateStoreError
from gitferry.transfer_plans import PlanConflict
from test_package_admission import counts, received


def setup(tmp_path, monkeypatch):
    admission, ref = received(tmp_path, monkeypatch)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name in ("admin", "author"):
        path = secrets / name
        path.write_text("synthetic-" + name + "-credential")
        path.chmod(0o600)
    decisions = PackageDecisions(admission, LocalReferences(secrets=secrets))
    base = {"target_instance_key": "b", "profile_revision": 1}
    initial = decisions.prerequisites(ref, TargetRequest(**base))
    intent = {**base, "expected_digest": initial["digest_sha256"]}
    return decisions, ref, initial, intent


def approve_realm(decisions, ref, initial, intent):
    proposal = initial["realms"][0]
    return decisions.confirm_realm(
        ref,
        RealmRequest(
            **intent,
            realm_key=proposal["realm_key"],
            display_name="Synthetic local realm",
            external_marker="operator-accepted-source-native-realm",
        ),
    )


def remote(monkeypatch, account=None, hook=None):
    import gitferry.target_accounts as module

    value = account or {"id": 9007199254740993, "login": "target.author", "is_admin": False}
    calls = []

    def probe(service, key, **kwargs):
        with service.store.read_connection() as connection:
            row = connection.execute(
                "SELECT approved_identity FROM connection_checks WHERE instance_key=?", (key,)
            ).fetchone()
        return {"requires_approval": False, "observed_identity": json.loads(row[0])}

    async def read(client, path):
        calls.append((str(client.base_url), path))
        if hook:
            hook()
        return value, "cert"

    monkeypatch.setattr(module, "probe_connection", probe)
    monkeypatch.setattr(module, "_json", read)
    return calls


def test_prerequisites_are_read_only_and_names_do_not_bind(tmp_path, monkeypatch):
    service, ref, initial, intent = setup(tmp_path, monkeypatch)
    before = counts(service.admissions)
    again = service.prerequisites(
        ref, TargetRequest(**{k: intent[k] for k in ("target_instance_key", "profile_revision")})
    )
    assert again == initial
    assert all(item["binding"] is None for item in initial["identities"])
    assert initial["sender"]["registered"]
    assert not initial["realms"][0]["registered"]
    assert counts(service.admissions) == before


def test_sender_and_realm_confirmation_are_explicit_and_cannot_redirect(tmp_path, monkeypatch):
    service, ref, initial, intent = setup(tmp_path, monkeypatch)
    with service.state.transaction() as connection:
        connection.execute("DELETE FROM instances WHERE instance_key='a'")
    request = SenderRequest(
        **intent, display_name="Offline source", base_url="https://source.invalid"
    )
    result = service.confirm_sender(ref, request)
    assert result["sender"]["registered"]
    assert service.confirm_sender(ref, request) == result
    with pytest.raises(StateStoreError):
        service.confirm_sender(
            ref, request.model_copy(update={"base_url": "https://other.invalid"})
        )
    approved = approve_realm(service, ref, initial, intent)
    assert approved["realms"][0]["registered"]
    assert all(item["binding"] is None for item in approved["identities"])
    with pytest.raises(PackageAdmissionConflict):
        service.confirm_realm(
            ref,
            RealmRequest(
                **intent,
                realm_key="not-in-package",
                display_name="Unknown",
                external_marker="other",
            ),
        )
    with pytest.raises(StateStoreError):
        service.confirm_realm(
            ref,
            RealmRequest(
                **intent,
                realm_key=initial["realms"][0]["realm_key"],
                display_name="Redirected",
                external_marker="other",
            ),
        )
    assert counts(service.admissions)["operations"] == 0


def test_checked_account_binding_preserves_large_native_id_and_replays(tmp_path, monkeypatch):
    service, ref, initial, intent = setup(tmp_path, monkeypatch)
    approve_realm(service, ref, initial, intent)
    calls = remote(monkeypatch)
    check = service.check_account(
        ref, AccountRequest(**intent, target_login="target.author", credential_ref="author")
    )
    assert check["target_account_id"] == "9007199254740993"
    assert check["credential_checked"]
    assert counts(service.admissions)["identity_bindings"] == 0
    request = BindingRequest(
        **intent,
        selector=initial["identities"][0]["selector"],
        target_login="target.author",
        target_account_id=check["target_account_id"],
        credential_ref="author",
    )
    result = service.confirm_binding(ref, request)
    assert result["identities"][0]["binding"]["target_account_id"] == check["target_account_id"]
    assert service.confirm_binding(ref, request) == result
    assert counts(service.admissions)["identity_bindings"] == 1
    assert all(
        path == "user" and base == "https://recipient.invalid/api/v1/" for base, path in calls
    )
    assert counts(service.admissions)["operations"] == 0
    assert counts(service.admissions)["object_origins"] == 0
    assert (
        service.state.get_identity_binding(
            result["identities"][0]["binding"]["binding_id"]
        ).target_account_id
        == 9007199254740993
    )


@pytest.mark.parametrize(
    "change",
    [
        "wrong-id",
        "other-selector",
        "stale-digest",
        "stale-profile",
        "target-admin",
        "other-user",
        "missing-secret",
    ],
)
def test_invalid_binding_never_grants_authority(tmp_path, monkeypatch, change):
    service, ref, initial, intent = setup(tmp_path, monkeypatch)
    approve_realm(service, ref, initial, intent)
    values = dict(
        intent,
        selector=initial["identities"][0]["selector"],
        target_login="target.author",
        target_account_id="9007199254740993",
        credential_ref="author",
    )
    account = {"id": 9007199254740993, "login": "target.author", "is_admin": False}
    if change == "wrong-id":
        values["target_account_id"] = "9007199254740994"
    if change == "other-selector":
        values["selector"] = {**values["selector"], "external_id": "other"}
    if change == "stale-digest":
        values["expected_digest"] = "0" * 64
    if change == "stale-profile":
        values["profile_revision"] = 2
    if change == "target-admin":
        account["is_admin"] = True
    if change == "other-user":
        account["login"] = "other.user"
    if change == "missing-secret":
        values["credential_ref"] = "absent"
    remote(monkeypatch, account)
    before = counts(service.admissions)
    with pytest.raises(
        (PackageAdmissionConflict, ConnectionUnavailable, ProvisioningTargetConflict, PlanConflict)
    ):
        service.confirm_binding(ref, BindingRequest(**values))
    assert counts(service.admissions) == before


def test_existing_binding_cannot_be_redirected_or_claimed_by_another_author(tmp_path, monkeypatch):
    service, ref, initial, intent = setup(tmp_path, monkeypatch)
    approve_realm(service, ref, initial, intent)
    remote(monkeypatch)
    values = dict(
        intent,
        selector=initial["identities"][0]["selector"],
        target_login="target.author",
        target_account_id="9007199254740993",
        credential_ref="author",
    )
    service.confirm_binding(ref, BindingRequest(**values))
    with pytest.raises(BindingConflict):
        service.confirm_binding(
            ref, BindingRequest(**{**values, "selector": initial["identities"][1]["selector"]})
        )
    remote(monkeypatch, {"id": 42, "login": "target.author", "is_admin": False})
    with pytest.raises(BindingConflict):
        service.confirm_binding(ref, BindingRequest(**{**values, "target_account_id": "42"}))
    assert counts(service.admissions)["identity_bindings"] == 1


def test_changed_approval_during_remote_read_blocks_binding(tmp_path, monkeypatch):
    service, ref, initial, intent = setup(tmp_path, monkeypatch)
    approve_realm(service, ref, initial, intent)

    def changed():
        with service.state.transaction() as connection:
            row = connection.execute(
                "SELECT approved_identity FROM connection_checks WHERE instance_key='b'"
            ).fetchone()
            document = json.loads(row[0])
            document["login"] = "another.technical"
            value = json.dumps(document, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE connection_checks SET approved_identity=?,observed_identity=? "
                "WHERE instance_key='b'",
                (value, value),
            )

    remote(monkeypatch, hook=changed)
    with pytest.raises(PackageAdmissionConflict, match="approval changed"):
        service.confirm_binding(
            ref,
            BindingRequest(
                **intent,
                selector=initial["identities"][0]["selector"],
                target_login="target.author",
                target_account_id="9007199254740993",
                credential_ref="author",
            ),
        )
    assert counts(service.admissions)["identity_bindings"] == 0


def test_existing_registration_methods_join_atomic_transaction(tmp_path, monkeypatch):
    service, _, _, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="rollback"):
        with service.state.transaction() as connection:
            service.state.register_instance(
                "local-other",
                "Other",
                "https://other.invalid",
                "install-other",
                connection=connection,
            )
            service.state.register_realm(
                "local-realm", "Realm", "local-marker", connection=connection
            )
            service.state.confirm_identity_binding(
                binding_id="bound",
                source_realm_key="local-realm",
                source_login="user",
                source_external_id="external",
                source_ou_path="/",
                target_instance_key="local-other",
                target_account_id=10,
                target_login="target",
                mapping_version=1,
                connection=connection,
            )
            raise RuntimeError("rollback")
    for method, identifier in (
        (service.state.get_instance, "local-other"),
        (service.state.get_realm, "local-realm"),
        (service.state.get_identity_binding, "bound"),
    ):
        with pytest.raises(KeyError):
            method(identifier)


def test_admin_api_requires_explicit_fields_and_csrf_without_admitting_facts(tmp_path, monkeypatch):
    service, ref, initial, intent = setup(tmp_path, monkeypatch)
    auth = AuthService(service.state)
    auth.bootstrap_admin("synthetic.admin", "synthetic local decision password")
    app = create_app(auth_service=auth)
    app.state.runtime = SimpleNamespace(store=service.state, paths=service.admissions.paths)
    client = TestClient(app, base_url="https://testserver")
    route = f"/api/v1/packages/{ref}/prerequisites"
    base = {k: intent[k] for k in ("target_instance_key", "profile_revision")}
    assert client.post(route, json=base).status_code == 401
    client.post(
        "/api/v1/auth/login",
        json={"username": "synthetic.admin", "password": "synthetic local decision password"},
    )
    assert client.post(route, json=base).status_code == 403
    headers = {"x-gitferry-csrf": client.cookies["gitferry_csrf"]}
    response = client.post(route, json=base, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == initial
    assert client.post(route, json={**base, "approved": True}, headers=headers).status_code == 400
    assert (
        client.post(route, json={**base, "profile_revision": True}, headers=headers).status_code
        == 400
    )
    assert counts(service.admissions)["identity_bindings"] == 0
    assert counts(service.admissions)["package_receipts"] == 0
