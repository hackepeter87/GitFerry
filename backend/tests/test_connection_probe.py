import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
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


def test_total_deadline_cancels_slow_drip_and_closes_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, references = setup(tmp_path)
    monkeypatch.setattr("gitferry.connection_probe.CHECK_DEADLINE_SECONDS", 0.1)
    closed = []
    calls = []

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            while True:
                yield b" "
                await asyncio.sleep(0.01)

        async def aclose(self) -> None:
            closed.append(True)

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, stream=Drip())

    started = time.monotonic()
    with pytest.raises(ConnectionUnavailable):
        probe_connection(
            service,
            "a",
            expected_revision=1,
            references=references,
            transport=httpx.MockTransport(handle),
        )
    assert time.monotonic() - started < 2
    assert closed == [True] and calls == ["/subpath/api/v1/version"]
    assert not service.get("a").locally_approved


def test_deadline_is_shared_across_both_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, references = setup(tmp_path)
    monkeypatch.setattr("gitferry.connection_probe.CHECK_DEADLINE_SECONDS", 0.1)
    calls = []

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        await asyncio.sleep(0.06)
        return httpx.Response(200, json={"version": "1.27.2", "id": 1, "login": "synthetic"})

    with pytest.raises(ConnectionUnavailable):
        probe_connection(
            service,
            "a",
            expected_revision=1,
            references=references,
            transport=httpx.MockTransport(handle),
        )
    assert calls == ["/subpath/api/v1/version", "/subpath/api/v1/user"]


def test_encoded_response_rejected_before_reading_body(tmp_path: Path) -> None:
    service, references = setup(tmp_path)
    consumed = []

    class Encoded(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            consumed.append(True)
            yield b"compressed data must never reach the decoder"

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Encoded())

    with pytest.raises(ConnectionUnavailable):
        probe_connection(
            service,
            "a",
            expected_revision=1,
            references=references,
            transport=httpx.MockTransport(handle),
        )
    assert not consumed


def test_certificate_rotation_returns_reviewable_previous_and_current_evidence(
    tmp_path: Path,
) -> None:
    service, references = setup(tmp_path)
    certificate = b"synthetic certificate one"

    class Peer:
        def get_extra_info(self, name: str) -> "Peer":
            return self

        def getpeercert(self, binary_form: bool) -> bytes:
            return certificate

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"version": "1.27.2", "id": 1, "login": "synthetic"},
            extensions={"network_stream": Peer()},
        )
    )
    first = probe_connection(
        service, "a", expected_revision=1, references=references, transport=transport
    )
    assert first["approved_identity"] is None
    approve_connection(service, "a", expected_revision=1, evidence_digest=first["evidence_digest"])
    certificate = b"synthetic certificate two"
    changed = probe_connection(
        service, "a", expected_revision=1, references=references, transport=transport
    )
    assert changed["changed_fields"] == ["certificate_sha256"]
    assert changed["approved_identity"] == first["observed_identity"]
    assert (
        changed["observed_identity"]["certificate_sha256"]
        == hashlib.sha256(certificate).hexdigest()
    )
    assert changed["requires_approval"] and not service.get("a").locally_approved
    service.update(
        "a",
        expected_revision=1,
        display_name="Renamed",
        credential_ref="synthetic-token",
        ca_ref=None,
    )
    revised = probe_connection(
        service, "a", expected_revision=2, references=references, transport=transport
    )
    assert revised["approved_identity"] == first["observed_identity"]


def test_cli_review_and_approval_use_current_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from gitferry.cli import main

    service, references = setup(tmp_path)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"version": "1.27.2", "id": 1, "login": "synthetic"}
        )
    )
    monkeypatch.setattr(
        "gitferry.profile_cli.probe_connection",
        lambda state, key, **kwargs: probe_connection(
            state, key, references=references, transport=transport, **kwargs
        ),
    )
    prefix = ["connections", "--database", str(tmp_path / "state.sqlite3")]
    assert main(prefix + ["test", "a", "--expected-revision", "1"]) == 0
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["observed_identity"]["ca_digest"] == "system-trust"
    assert evidence["approved_identity"] is None
    assert (
        main(prefix + ["approve", "a", "--expected-revision", "1", "--evidence-digest", "0" * 64])
        == 2
    )
    assert not service.get("a").locally_approved
    capsys.readouterr()
    assert (
        main(
            prefix
            + [
                "approve",
                "a",
                "--expected-revision",
                "1",
                "--evidence-digest",
                evidence["evidence_digest"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["locally_approved"]
    assert service.get("a").locally_approved


def test_invalid_system_trust_override_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, references = setup(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca.pem"))
    with pytest.raises(ConnectionUnavailable):
        references.tls(None)
