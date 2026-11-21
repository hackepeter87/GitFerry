import subprocess
import sys
from pathlib import Path

import pytest
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.directory_settings import (
    DirectorySettingsConflict,
    DirectorySettingsError,
    DirectorySettingsService,
)
from gitferry.state_store import StateStore


def make_service(path: Path) -> DirectorySettingsService:
    store = StateStore(path)
    profile_service = ProfileService(store)
    profile_service.create(
        ConnectionProfile(
            "target", "Synthetic", "https://forge.invalid", "install-1", "token-ref", "gitea-ca"
        )
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE connection_profiles SET locally_approved=1 WHERE instance_key='target'"
        )
        store.register_realm(
            "SYNTHETIC-OPENLDAP", "Synthetic LDAP", "realm-marker", connection=connection
        )
    return DirectorySettingsService(store, profile_service)


SOURCE = {
    "name": "synthetic-openldap",
    "type": "ldaps",
    "mode": "simple",
    "directoryRealm": "SYNTHETIC-OPENLDAP",
    "host": "ldap.example.invalid",
    "port": 636,
    "userDn": "uid=%s,ou=People,dc=example,dc=invalid",
    "userFilter": "(&(objectClass=posixAccount)(uid=%s))",
    "emailAttribute": "mail",
    "usernameAttribute": "uid",
    "caSecretRef": "trust/directory-ca",
}


def test_save_is_cas_idempotent_and_secret_free(tmp_path: Path) -> None:
    service = make_service(tmp_path / "state.sqlite3")
    first = service.save("target", SOURCE, None, "operator.synthetic")
    assert first["revision"] == 1
    assert first["status"] == "ready-for-target-local-bootstrap"
    replay = service.save("target", SOURCE, 1, "operator.synthetic")
    assert replay["revision"] == 1
    changed = service.save(
        "target", {**SOURCE, "host": "ldap2.example.invalid"}, 1, "operator.synthetic"
    )
    assert changed["revision"] == 2
    with pytest.raises(DirectorySettingsConflict, match="changed"):
        service.save("target", SOURCE, 1, "operator.synthetic")
    with pytest.raises(DirectorySettingsError, match="raw secret"):
        service.save(
            "target", {**SOURCE, "bindPassword": "synthetic-only"}, 2, "operator.synthetic"
        )


def test_export_and_import_check_exact_profile_and_registered_realm(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    service = make_service(path)
    service.save("target", SOURCE, None, "operator.synthetic")
    blueprint = service.export("target", "synthetic-openldap")
    assert blueprint["spec"]["gitea"] == {
        "baseUrl": "https://forge.invalid",
        "credentialSecretRef": "token-ref",
        "caSecretRef": "gitea-ca",
    }
    assert blueprint["spec"]["authenticationSources"][0]["caSecretRef"] == "trust/directory-ca"
    blueprint["spec"]["gitea"]["baseUrl"] = "https://other.invalid"
    with pytest.raises(DirectorySettingsConflict, match="does not match"):
        service.import_blueprint("target", blueprint, 1, "operator.synthetic")


def test_binddn_is_visible_as_unsupported_automation(tmp_path: Path) -> None:
    service = make_service(tmp_path / "state.sqlite3")
    source = {
        key: value
        for key, value in SOURCE.items()
        if key not in {"mode", "userDn", "userFilter", "emailAttribute", "caSecretRef"}
    }
    source["bindSecretRef"] = "secret/ldap-bind"
    result = service.save("target", source, None, "operator.synthetic")
    assert result["status"] == "unsupported-automation"
    assert "admin UI" in result["prerequisites"][0]


def test_store_survives_real_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    service = make_service(path)
    service.save("target", SOURCE, None, "operator.synthetic")
    result = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic temporary database
        [
            sys.executable,
            "-c",
            "import sys; from gitferry.state_store import StateStore; "
            "s=StateStore(sys.argv[1]); "
            "c=s.read_connection(); row=c.__enter__().execute("
            "'SELECT config_revision FROM directory_configurations').fetchone(); "
            "print(row[0]); c.__exit__(None,None,None)",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    assert result.stdout.strip() == "1"


@pytest.mark.parametrize(
    "change", [{"userDn": None}, {"extra": "not supported"}, {"type": "http"}, {"port": True}]
)
def test_invalid_source_never_reaches_state(tmp_path, change):
    service = make_service(tmp_path / "state.sqlite3")
    with pytest.raises(DirectorySettingsError):
        service.save("target", {**SOURCE, **change}, None)
    assert service.list() == []


def test_database_constraints_protect_directory_identity(tmp_path):
    import sqlite3

    service = make_service(tmp_path / "state.sqlite3")
    service.save("target", SOURCE, None)
    with pytest.raises(sqlite3.IntegrityError), service.store.transaction() as connection:
        connection.execute("UPDATE directory_configurations SET directory_realm='missing'")
    assert service.get("target", SOURCE["name"])["realm_key"] == SOURCE["directoryRealm"]


def test_directory_api_auth_csrf_and_no_server_authority(tmp_path):
    from fastapi.testclient import TestClient
    from gitferry.api import create_app
    from gitferry.auth import AuthService

    service = make_service(tmp_path / "state.sqlite3")
    auth = AuthService(service.store)
    auth.bootstrap_admin("operator", "synthetic-directory-admin-passphrase")
    with TestClient(create_app(auth_service=auth), base_url="https://testserver") as client:
        assert client.get("/api/v1/directories").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "operator", "password": "synthetic-directory-admin-passphrase"},
            ).status_code
            == 200
        )
        value = {"instance_key": "target", "source": SOURCE, "expected_revision": None}
        assert client.post("/api/v1/directories", json=value).status_code == 403
        headers = {"x-gitferry-csrf": client.cookies["gitferry_csrf"]}
        response = client.post("/api/v1/directories", json=value, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready-for-target-local-bootstrap"
        assert (
            client.post(
                "/api/v1/directories", json={**value, "approved": True}, headers=headers
            ).status_code
            == 400
        )
        exported = client.get("/api/v1/directories/target/synthetic-openldap/export")
        assert exported.status_code == 200
        with service.store.read_connection() as connection:
            assert (
                connection.execute("SELECT count(*) FROM directory_bootstraps").fetchone()[0] == 0
            )
            actor = connection.execute(
                "SELECT actor FROM directory_configuration_events"
            ).fetchone()[0]
            assert actor != "local-cli"


def test_explicit_resave_refreshes_approved_profile_revision(tmp_path):
    service = make_service(tmp_path / "state.sqlite3")
    service.save("target", SOURCE, None)
    with service.store.transaction() as connection:
        connection.execute(
            "UPDATE connection_profiles SET revision=2, credential_ref='rotated-ref' "
            "WHERE instance_key='target'"
        )
    reviewed = service.save("target", SOURCE, 1)
    assert reviewed["revision"] == 2
    assert reviewed["profile_revision"] == 2
    assert service.save("target", SOURCE, 2) == reviewed


def test_simple_mode_cannot_use_bind_reference_to_bypass_required_fields(tmp_path):
    service = make_service(tmp_path / "state.sqlite3")
    invalid = {
        key: value
        for key, value in SOURCE.items()
        if key not in {"userDn", "userFilter", "emailAttribute"}
    }
    invalid["bindSecretRef"] = "bind-reference"
    with pytest.raises(DirectorySettingsError):
        service.save("target", invalid, None)
    service.save("target", SOURCE, None)
    document = service.export("target", SOURCE["name"])
    document["spec"]["authenticationSources"] = [invalid]
    with pytest.raises(DirectorySettingsError):
        service.import_blueprint("target", document, 1)
    assert service.get("target", SOURCE["name"])["revision"] == 1
