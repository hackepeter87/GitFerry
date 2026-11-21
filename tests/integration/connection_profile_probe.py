"""Connection-profile HTTPS evidence against the run-owned real Gitea target."""

from __future__ import annotations

import io
import json
import logging
import os
import ssl
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.connection_probe import (
    ConnectionUnavailable,
    LocalReferences,
    approve_connection,
    probe_connection,
)
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.state_store import StateStore
from support import ADMIN_USERNAME, TARGET_URL, ProbeFailure, api, run


def certificate(directory: Path, name: str, *, expired: bool = False) -> tuple[Path, Path]:
    key, csr, cert = (directory / f"{name}.{suffix}" for suffix in ("key", "csr", "pem"))
    extension = directory / f"{name}.ext"
    extension.write_text("subjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\n")
    run(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(csr),
            "-subj",
            "/CN=localhost",
        ]
    )
    key.chmod(0o600)
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-signkey",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-extfile",
            str(extension),
        ]
    )
    if expired:
        issuer = directory / f"{name}-issuer.pem"
        issuer.write_bytes(cert.read_bytes())
        (directory / "index").write_text("")
        (directory / "serial").write_text("01\n")
        config = directory / "ca.conf"
        config.write_text(
            f'[ca]\ndefault_ca=local\n[local]\ndatabase="{directory}/index"\n'
            f'serial="{directory}/serial"\nnew_certs_dir="{directory}"\n'
            "default_md=sha256\npolicy=names\n[names]\ncommonName=supplied\n"
        )
        run(
            [
                "openssl",
                "ca",
                "-batch",
                "-selfsign",
                "-config",
                str(config),
                "-keyfile",
                str(key),
                "-cert",
                str(issuer),
                "-in",
                str(csr),
                "-out",
                str(cert),
                "-startdate",
                "20000101000000Z",
                "-enddate",
                "20000102000000Z",
                "-extfile",
                str(extension),
            ]
        )
    return cert, key


@contextmanager
def tls_proxy(cert: Path, key: Path) -> Iterator[tuple[str, dict[str, int]]]:
    calls = {"get": 0, "write": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            calls["get"] += 1
            if self.path not in {"/api/v1/version", "/api/v1/user"}:
                self.send_error(404)
                return
            response = httpx.get(
                TARGET_URL + self.path,
                headers={"Authorization": self.headers.get("Authorization", "")},
                timeout=10,
                trust_env=False,
            )
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def do_POST(self) -> None:
            calls["write"] += 1
            self.send_error(405)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise ProbeFailure("TLS proxy failed to stop")


def run_connection_profile_probe() -> dict[str, Any]:
    token = api(
        TARGET_URL,
        "POST",
        f"/users/{ADMIN_USERNAME}/tokens",
        json_body={"name": "synthetic-connection-probe", "scopes": ["read:user"]},
    )
    assertions: dict[str, bool] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="gitferry-profile-probe-") as temporary:
            directory = Path(temporary).resolve()
            cert, key = certificate(directory, "trusted")
            wrong_cert, _ = certificate(directory, "wrong")
            expired_cert, expired_key = certificate(directory, "expired", expired=True)
            (directory / "token").write_text(token["sha1"])
            (directory / "token").chmod(0o600)
            references = LocalReferences(directory, directory)
            service = ProfileService(StateStore(directory / "state.sqlite3"))
            with tls_proxy(cert, key) as (url, calls):
                service.create(
                    ConnectionProfile(
                        "target", "Synthetic target", url, "target-installation", "token", cert.name
                    )
                )
                result = probe_connection(
                    service, "target", expected_revision=1, references=references
                )
                assertions["real_gitea_version_and_account"] = (
                    result["version"] == "1.27.2" and result["login"] == ADMIN_USERNAME
                )
                assertions["check_did_not_approve"] = not service.get("target").locally_approved
                approve_connection(
                    service,
                    "target",
                    expected_revision=1,
                    evidence_digest=result["evidence_digest"],
                )
                assertions["explicit_local_approval"] = service.get("target").locally_approved
                assertions["read_only_connection_check"] = calls == {"get": 2, "write": 0}
                captured = io.StringIO()
                handler = logging.StreamHandler(captured)
                logging.getLogger().addHandler(handler)
                try:
                    with patch.dict(
                        os.environ,
                        {"GITFERRY_SECRET_DIR": str(directory), "GITFERRY_CA_DIR": str(directory)},
                    ):
                        auth = AuthService(service.store)
                        auth.bootstrap_admin("synthetic.admin", "synthetic integration passphrase")
                        with TestClient(
                            create_app(auth_service=auth), base_url="https://testserver"
                        ) as client:
                            login = client.post(
                                "/api/v1/auth/login",
                                json={
                                    "username": "synthetic.admin",
                                    "password": "synthetic integration passphrase",
                                },
                            )
                            if login.status_code != 200:
                                raise ProbeFailure("Connection API login failed")
                            headers = {"x-gitferry-csrf": client.cookies["gitferry_csrf"]}
                            response = client.post(
                                "/api/v1/connections/target/test",
                                json={"expected_revision": 1},
                                headers=headers,
                            )
                            if response.status_code != 200:
                                raise ProbeFailure("Real Gitea API-boundary check failed")
                            observed = response.json()
                            approval = client.post(
                                "/api/v1/connections/target/approve",
                                json={
                                    "expected_revision": 1,
                                    "evidence_digest": observed["evidence_digest"],
                                },
                                headers=headers,
                            )
                            assertions["api_probe_and_local_approval"] = (
                                approval.status_code == 200
                                and approval.json()["locally_approved"]
                                and observed["observed_identity"] == result["observed_identity"]
                            )
                            api_output = (
                                response.text
                                + approval.text
                                + client.get("/api/v1/connections").text
                            )
                        command = [
                            sys.executable,
                            "-c",
                            "from gitferry.cli import main; raise SystemExit(main())",
                            "connections",
                            "--database",
                            str(directory / "state.sqlite3"),
                            "test",
                            "target",
                            "--expected-revision",
                            "1",
                        ]
                        checked = subprocess.run(
                            command, capture_output=True, text=True, timeout=30, check=True
                        )
                        cli_evidence = json.loads(checked.stdout)
                        assertions["cli_restart_preserves_approved_evidence"] = (
                            cli_evidence["observed_identity"] == observed["observed_identity"]
                            and not cli_evidence["requires_approval"]
                        )
                        assertions["credentials_absent_from_boundaries_and_logs"] = all(
                            token["sha1"] not in value
                            for value in (
                                api_output,
                                checked.stdout,
                                checked.stderr,
                                json.dumps(command),
                                captured.getvalue(),
                                json.dumps(assertions),
                            )
                        )
                finally:
                    logging.getLogger().removeHandler(handler)
                # Exercise the default trust-store path offline, without an explicit
                # per-profile CA. The synthetic CA is installed only for this subprocess scope.
                system = ProfileService(StateStore(directory / "system-trust.sqlite3"))
                system.create(ConnectionProfile("system", "System trust", url, "system", "token"))
                with patch.dict(os.environ, {"SSL_CERT_FILE": str(cert)}):
                    system_result = probe_connection(
                        system, "system", expected_revision=1, references=references
                    )
                assertions["system_trust_store_without_ca_reference"] = (
                    system_result["version"] == "1.27.2"
                    and system_result["observed_identity"]["ca_digest"] == "system-trust"
                )
                for label, ca, endpoint in (
                    ("untrusted_ca_rejected", None, url),
                    ("wrong_ca_rejected", wrong_cert.name, url),
                    (
                        "hostname_mismatch_rejected",
                        cert.name,
                        url.replace("localhost", "127.0.0.1"),
                    ),
                ):
                    other = ProfileService(StateStore(directory / f"{label}.sqlite3"))
                    other.create(ConnectionProfile(label, label, endpoint, label, "token", ca))
                    before = calls["get"]
                    try:
                        probe_connection(other, label, expected_revision=1, references=references)
                    except ConnectionUnavailable:
                        assertions[label] = calls["get"] == before
                    else:
                        assertions[label] = False
            with tls_proxy(expired_cert, expired_key) as (url, calls):
                expired = ProfileService(StateStore(directory / "expired.sqlite3"))
                expired.create(
                    ConnectionProfile(
                        "expired", "Expired", url, "expired", "token", expired_cert.name
                    )
                )
                try:
                    probe_connection(expired, "expired", expected_revision=1, references=references)
                except ConnectionUnavailable:
                    assertions["expired_certificate_rejected"] = calls["get"] == 0
                else:
                    assertions["expired_certificate_rejected"] = False
            assertions["credentials_absent_from_state_and_exports"] = token[
                "sha1"
            ] not in json.dumps(service.export_configuration()) and all(
                token["sha1"].encode() not in path.read_bytes()
                for path in directory.glob("*.sqlite3*")
            )
            if not all(assertions.values()):
                raise ProbeFailure(f"Connection profile assertions failed: {assertions}")
            return {
                "status": "passed",
                "gitea_version": result["version"],
                "assertions": assertions,
                "tls_boundary": "generated localhost HTTPS proxy to isolated real Gitea target",
                "limits": [
                    "Operator registration is not remote installation attestation",
                    "Probe checks version and authenticated account only",
                    "Default system trust uses a synthetic CA fixture; "
                    "no external public-CA service is contacted",
                ],
            }
    finally:
        api(TARGET_URL, "DELETE", f"/users/{ADMIN_USERNAME}/tokens/{token['id']}")
