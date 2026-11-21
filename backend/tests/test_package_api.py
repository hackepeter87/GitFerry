from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.runtime import RuntimeContext
from gitferry.state_store import StateStore
from test_package_admission import counts, received


class _Admissions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def list(self) -> list[dict[str, object]]:
        return [{"package_ref": "synthetic-package"}]

    def review(self, package_ref: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("review", package_ref, kwargs))
        return {"status": "reviewed"}

    def admit(self, package_ref: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("admit", package_ref, kwargs))
        return {"status": "admitted-facts-only"}

    def plan_review(self, package_ref: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("plan-review", package_ref, kwargs))
        return {"review_digest": "synthetic-review"}

    def prepare_plan(self, package_ref: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("prepare-plan", package_ref, kwargs))
        return {"status": "draft"}


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, str, _Admissions]:
    state = StateStore(tmp_path / "state.sqlite3")
    auth = AuthService(state)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    app = create_app(auth_service=auth)
    service = _Admissions()
    monkeypatch.setattr("gitferry.package_api._service", lambda request: service)
    client = TestClient(app, base_url="https://testserver")
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "synthetic.admin", "password": "correct synthetic passphrase"},
    )
    assert login.status_code == 200
    return client, client.cookies["gitferry_csrf"], service


def test_package_routes_require_admin_csrf_and_forward_review_options(
    tmp_path: Path, monkeypatch
) -> None:
    client, csrf, service = _client(tmp_path, monkeypatch)
    with client:
        path = "/api/v1/packages/synthetic-package/plan-review"
        body = {
            "target_instance_key": "synthetic-target",
            "profile_revision": 7,
            "author_credential_refs": {"binding-a": "credential-a"},
        }
        assert client.get("/api/v1/packages").status_code == 200
        assert client.post(path, json=body).status_code == 403
        response = client.post(path, json=body, headers={"x-gitferry-csrf": csrf})
        assert response.status_code == 200
        assert service.calls[-1] == (
            "plan-review",
            "synthetic-package",
            {
                "target_instance_key": "synthetic-target",
                "profile_revision": 7,
                "include_metadata": True,
                "apply_members": False,
                "author_credential_refs": {"binding-a": "credential-a"},
            },
        )


def test_package_routes_reject_unknown_fields_and_forward_expected_digests(
    tmp_path: Path, monkeypatch
) -> None:
    client, csrf, service = _client(tmp_path, monkeypatch)
    with client:
        headers = {"x-gitferry-csrf": csrf}
        invalid = client.post(
            "/api/v1/packages/p/admit",
            json={
                "target_instance_key": "t",
                "profile_revision": 1,
                "expected_digest": "d",
                "extra": 1,
            },
            headers=headers,
        )
        assert invalid.status_code == 400
        prepared = client.post(
            "/api/v1/packages/p/prepare-plan",
            json={
                "target_instance_key": "t",
                "profile_revision": 2,
                "expected_review_digest": "review-digest",
            },
            headers=headers,
        )
        assert prepared.status_code == 200
        assert service.calls[-1][2]["expected_review_digest"] == "review-digest"
        assert service.calls[-1][2]["profile_revision"] == 2


def test_package_conflicts_are_sanitized(tmp_path: Path, monkeypatch) -> None:
    client, csrf, _ = _client(tmp_path, monkeypatch)

    def conflict(request):
        from gitferry.package_admission import PackageAdmissionConflict

        raise PackageAdmissionConflict("secret synthetic path")

    monkeypatch.setattr("gitferry.package_api._service", conflict)
    with client:
        response = client.post(
            "/api/v1/packages/p/review",
            json={"target_instance_key": "t", "profile_revision": 1},
            headers={"x-gitferry-csrf": csrf},
        )
    assert response.status_code == 409
    assert "secret synthetic path" not in response.text


def test_authenticated_real_admission_does_not_create_operations_or_approve_authors(
    tmp_path: Path, monkeypatch
) -> None:
    service, ref = received(tmp_path, monkeypatch)
    auth = AuthService(service.state)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    app = create_app(auth_service=auth)
    app.state.runtime = RuntimeContext(service.paths, service.state)
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/v1/packages").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "synthetic.admin", "password": "correct synthetic passphrase"},
            ).status_code
            == 200
        )
        headers = {"x-gitferry-csrf": client.cookies["gitferry_csrf"]}
        common = {"target_instance_key": "b", "profile_revision": 1}
        review = client.post(f"/api/v1/packages/{ref}/review", json=common, headers=headers)
        assert review.status_code == 200
        assert counts(service)["package_receipts"] == 0
        response = client.post(
            f"/api/v1/packages/{ref}/admit",
            json={**common, "expected_digest": review.json()["digest_sha256"]},
            headers=headers,
        )
        assert response.status_code == 200
        assert counts(service)["package_receipts"] == 1
        assert counts(service)["identity_bindings"] == counts(service)["operations"] == 0
        stale = client.post(
            f"/api/v1/packages/{ref}/review",
            json={**common, "profile_revision": 2},
            headers=headers,
        )
        assert stale.status_code == 409
