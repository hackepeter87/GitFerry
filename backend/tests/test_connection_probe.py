from pathlib import Path

import httpx
import pytest
from gitferry.connection_probe import (
    ConnectionUnavailable,
    LocalReferences,
    approve_connection,
    probe_connection,
)
from gitferry.connection_profiles import ConnectionProfile, ProfileConflict, ProfileService
from gitferry.state_store import StateStore


def setup(tmp_path: Path) -> tuple[ProfileService, LocalReferences]:
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "synthetic-token").write_text("synthetic-test-token\n")
    (secret_dir / "synthetic-token").chmod(0o600)
    service = ProfileService(StateStore(tmp_path / "state.sqlite3"))
    service.create(
        ConnectionProfile(
            "a", "Synthetic", "https://forge.invalid/subpath", "install-a", "synthetic-token"
        )
    )
    return service, LocalReferences(secret_dir.resolve(), tmp_path.resolve())


def test_probe_requires_local_approval_and_identity_change_revokes_it(tmp_path: Path) -> None:
    service, references = setup(tmp_path)
    account_id = 42
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["authorization"] == "token synthetic-test-token"
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"version": "1.27.2"}
            if request.url.path.endswith("version")
            else {"id": account_id, "login": "synthetic-admin"},
        )

    transport = httpx.MockTransport(handle)
    evidence = probe_connection(
        service, "a", expected_revision=1, references=references, transport=transport
    )
    assert calls == ["/subpath/api/v1/version", "/subpath/api/v1/user"]
    assert evidence["requires_approval"] and not service.get("a").locally_approved
    approve_connection(
        service, "a", expected_revision=1, evidence_digest=evidence["evidence_digest"]
    )
    assert service.get("a").locally_approved
    assert not probe_connection(
        service, "a", expected_revision=1, references=references, transport=transport
    )["requires_approval"]
    account_id = 43
    changed = probe_connection(
        service, "a", expected_revision=1, references=references, transport=transport
    )
    assert changed["requires_approval"] and not service.get("a").locally_approved
    with pytest.raises(ProfileConflict):
        approve_connection(
            service, "a", expected_revision=1, evidence_digest=evidence["evidence_digest"]
        )
    with service.store.read_connection() as connection:
        saved = connection.execute("SELECT * FROM connection_checks").fetchone()
        assert saved["approved_identity"] != saved["observed_identity"]
        assert "synthetic-test-token" not in str(tuple(saved))


@pytest.mark.parametrize("status", [301, 302, 401, 403, 500])
def test_remote_errors_do_not_follow_redirects_or_expose_remote_body(
    tmp_path: Path, status: int
) -> None:
    service, references = setup(tmp_path)
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(
            status, headers={"location": "http://other.invalid"}, text="synthetic-test-token"
        )

    with pytest.raises(ConnectionUnavailable) as error:
        probe_connection(
            service,
            "a",
            expected_revision=1,
            references=references,
            transport=httpx.MockTransport(handle),
        )
    assert "synthetic-test-token" not in str(error.value)
    assert len(calls) == 1 and not service.get("a").locally_approved


def test_missing_secret_invalidates_old_probe_without_losing_approval_baseline(
    tmp_path: Path,
) -> None:
    service, references = setup(tmp_path)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "version": "1.27.2",
                "id": 42,
                "login": "synthetic",
            },
        )
    )
    evidence = probe_connection(
        service, "a", expected_revision=1, references=references, transport=transport
    )
    approve_connection(
        service, "a", expected_revision=1, evidence_digest=evidence["evidence_digest"]
    )
    (references.secrets / "synthetic-token").unlink()
    with pytest.raises(ConnectionUnavailable):
        probe_connection(
            service, "a", expected_revision=1, references=references, transport=transport
        )
    with pytest.raises(ProfileConflict):
        approve_connection(
            service, "a", expected_revision=1, evidence_digest=evidence["evidence_digest"]
        )
    with service.store.read_connection() as connection:
        assert connection.execute("SELECT approved_identity FROM connection_checks").fetchone()[0]


def test_secret_permissions_and_symlink_fail_closed(tmp_path: Path) -> None:
    _, references = setup(tmp_path)
    path = references.secrets / "synthetic-token"
    path.chmod(0o644)
    with pytest.raises(ConnectionUnavailable):
        references.token("synthetic-token")
    path.chmod(0o600)
    (references.secrets / "link").symlink_to(path)
    with pytest.raises(ConnectionUnavailable):
        references.token("link")
