from pathlib import Path

from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.state_store import StateStore


def test_profiles_require_admin_csrf_and_reject_authority_injection(tmp_path: Path) -> None:
    auth = AuthService(StateStore(tmp_path / "state.sqlite3"))
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    with TestClient(create_app(auth_service=auth), base_url="https://testserver") as client:
        assert client.get("/api/v1/connections").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": "synthetic.admin",
                    "password": "correct synthetic passphrase",
                },
            ).status_code
            == 200
        )
        data = {
            "display_name": "Synthetic forge",
            "base_url": "https://forge.invalid",
            "installation_marker": "installation-a",
            "credential_ref": "forge-token",
            "ca_ref": None,
        }
        assert client.post("/api/v1/connections", json=data).status_code == 403
        headers = {"x-gitferry-csrf": client.cookies["gitferry_csrf"]}
        invalid = client.post(
            "/api/v1/connections", json={**data, "token": "synthetic"}, headers=headers
        )
        assert invalid.status_code == 422
        assert "synthetic" not in invalid.text
        result = client.post("/api/v1/connections", json=data, headers=headers)
        assert result.status_code == 201
        saved = result.json()
        assert client.get("/api/v1/connections").json() == [saved]
        exported = client.get("/api/v1/connections/export").json()
        assert "locally_approved" not in exported["profiles"][0]
        edited = client.patch(
            f"/api/v1/connections/{saved['instance_key']}",
            json={
                "expected_revision": 1,
                "display_name": "Renamed",
                "credential_ref": "rotated",
                "ca_ref": None,
            },
            headers=headers,
        )
        assert edited.status_code == 200 and edited.json()["revision"] == 2
        assert (
            client.post(
                f"/api/v1/connections/{saved['instance_key']}/retire",
                json={"expected_revision": 1},
                headers=headers,
            ).status_code
            == 409
        )
        assert client.get("/api/v1/connections").headers["cache-control"] == "no-store"
        exported["profiles"][0].update(
            instance_key="imported",
            base_url="https://imported.invalid",
            installation_marker="imported-installation",
        )
        imported = client.post("/api/v1/connections/import", json=exported, headers=headers)
        assert imported.status_code == 200 and not imported.json()[0]["locally_approved"]
        replaced = client.post(
            f"/api/v1/connections/{saved['instance_key']}/replace-installation",
            json={"expected_revision": 2, "installation_marker": "replacement-installation"},
            headers=headers,
        )
        assert replaced.status_code == 200
        replacement = replaced.json()
        assert replacement["instance_key"] != saved["instance_key"]
        assert not replacement["locally_approved"]
        retired = client.post(
            f"/api/v1/connections/{replacement['instance_key']}/retire",
            json={"expected_revision": 1},
            headers=headers,
        )
        assert retired.status_code == 200 and not retired.json()["enabled"]
        records = client.get("/api/v1/connections").json()
        assert sum(record["enabled"] for record in records) == 1
        assert len(records) == 3


def test_profile_input_is_bounded_and_duplicate_keys_rejected(tmp_path: Path) -> None:
    import pytest
    from gitferry.connection_profiles import ProfileValidationError
    from gitferry.profile_api import parse_configuration

    for data in (b'{"version":1,"version":2}', b"x" * (256 * 1024 + 1), b"[" * 2000):
        with pytest.raises(ProfileValidationError):
            parse_configuration(data)


def test_source_browse_boundary_requires_admin_csrf_and_local_only_routing(
    tmp_path: Path, monkeypatch
) -> None:
    from gitferry import profile_api

    calls = []

    def browse(service, key, **value):
        calls.append((key, value))
        return {"instance_key": key, "entries": [], "inventory_complete": False}

    monkeypatch.setattr(profile_api, "browse_source", browse)
    auth = AuthService(StateStore(tmp_path / "state.sqlite3"))
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    with TestClient(create_app(auth_service=auth), base_url="https://testserver") as client:
        path = "/api/v1/connections/source/browse"
        body = {"expected_revision": 1, "kind": "organizations", "organization": None, "page": 1}
        assert client.post(path, json=body).status_code == 401
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "synthetic.admin",
                "password": "correct synthetic passphrase",
            },
        )
        assert client.post(path, json=body).status_code == 403
        headers = {"x-gitferry-csrf": client.cookies["gitferry_csrf"]}
        assert (
            client.post(
                path, json={**body, "url": "https://untrusted.invalid"}, headers=headers
            ).status_code
            == 422
        )
        assert not calls
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200 and response.json()["inventory_complete"] is False
        assert calls == [("source", body)]
