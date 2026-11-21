"""Seed and verify synthetic durable state inside the packaged OCI runtime."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from gitferry.state_store import OriginKey, StateStore

DATABASE = Path("/var/lib/gitferry/state/gitferry.sqlite3")
PACKAGE_MARKER = Path("/var/lib/gitferry/packages/restart-proof.txt")
WORK_MARKER = Path("/var/lib/gitferry/work/restart-proof.txt")


def admin_security() -> dict[str, bool]:
    """Bootstrap the installed CLI and exercise the live HTTP auth boundary."""
    username = "oci-admin"
    password = secrets.token_urlsafe(32)
    password_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as stream:
            password_path = stream.name
            stream.write(password)
        os.chmod(password_path, 0o600)
        bootstrap = subprocess.run(  # noqa: S603 -- installed CLI with a private synthetic file
            [
                shutil.which("gitferry") or "/usr/local/bin/gitferry",
                "admin",
                "bootstrap",
                "--database",
                str(DATABASE),
                "--username",
                username,
                "--password-file",
                password_path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        cli_safe = bootstrap.returncode == 0 and password not in bootstrap.stdout + bootstrap.stderr
        if not cli_safe:
            return {
                "cli_admin_bootstrap": False,
                "cli_password_not_logged": False,
                "unauthenticated_plans_401": False,
                "secure_session_cookie": False,
                "http_only_session_cookie": False,
                "strict_session_cookie": False,
                "authenticated_session_200": False,
                "missing_csrf_403": False,
                "logout_204": False,
                "revoked_session_401": False,
            }

        def request(
            method: str,
            path: str,
            *,
            body: bytes | None = None,
            headers: dict[str, str] | None = None,
        ) -> tuple[int, dict[str, str], bytes]:
            connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=5)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            values = {
                "content-type": response.getheader("content-type", ""),
                "set-cookie": "\n".join(response.msg.get_all("Set-Cookie", [])),
            }
            result = (response.status, values, response.read())
            connection.close()
            return result

        unauthenticated_status, _, _ = request("GET", "/api/v1/plans")
        login_body = json.dumps({"username": username, "password": password}).encode()
        login_status, login_headers, _ = request(
            "POST",
            "/api/v1/auth/login",
            body=login_body,
            headers={"Content-Type": "application/json"},
        )
        cookies = login_headers["set-cookie"].splitlines()
        session_cookie = next(
            (
                line.split(";", 1)[0]
                for line in cookies
                if line.startswith("__Host-gitferry_session=")
            ),
            "",
        )
        csrf_cookie = next(
            (line.split(";", 1)[0] for line in cookies if line.startswith("gitferry_csrf=")),
            "",
        )
        session_attributes = (
            next((line for line in cookies if line.startswith("__Host-gitferry_session=")), "")
            .lower()
            .split(";")
        )
        attributes = {attribute.strip() for attribute in session_attributes[1:]}
        cookie_header = f"{session_cookie}; {csrf_cookie}"
        authenticated_status, _, _ = request(
            "GET", "/api/v1/auth/session", headers={"Cookie": cookie_header}
        )
        missing_csrf_status, _, _ = request(
            "POST", "/api/v1/auth/logout", headers={"Cookie": cookie_header}
        )
        logout_status, _, _ = request(
            "POST",
            "/api/v1/auth/logout",
            headers={"Cookie": cookie_header, "x-gitferry-csrf": csrf_cookie.split("=", 1)[1]},
        )
        revoked_status, _, _ = request(
            "GET", "/api/v1/auth/session", headers={"Cookie": cookie_header}
        )
        return {
            "cli_admin_bootstrap": True,
            "cli_password_not_logged": cli_safe,
            "unauthenticated_plans_401": unauthenticated_status == 401,
            "secure_session_cookie": "secure" in attributes,
            "http_only_session_cookie": "httponly" in attributes,
            "strict_session_cookie": "samesite=strict" in attributes,
            "authenticated_session_200": login_status == 200 and authenticated_status == 200,
            "missing_csrf_403": missing_csrf_status == 403,
            "logout_204": logout_status == 204,
            "revoked_session_401": revoked_status == 401,
        }
    finally:
        if password_path:
            Path(password_path).unlink(missing_ok=True)


def ui_snapshot() -> dict[str, bool]:
    def get(path: str) -> tuple[int, str, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        result = (response.status, response.getheader("content-type", ""), response.read())
        connection.close()
        return result

    root_status, root_type, root = get("/")
    script_match = re.search(rb'<script[^>]+src="(/assets/[^"]+\.js)"', root)
    asset_status = 0
    asset = b""
    if script_match is not None:
        asset_status, _, asset = get(script_match.group(1).decode("ascii"))
    api_status, _, api_body = get("/api/v1/auth/status")
    return {
        "root_html": root_status == 200
        and root_type.startswith("text/html")
        and b'<div id="app"></div>' in root,
        "javascript_asset": asset_status == 200 and b"Administrator bootstrap required" in asset,
        "bootstrap_api": api_status == 200 and json.loads(api_body) == {"bootstrapRequired": True},
    }


def seed() -> dict[str, Any]:
    state = StateStore(DATABASE)
    state.register_instance(
        "oci-instance-a", "Synthetic A", "https://a.example.invalid", "oci-install-a"
    )
    state.register_instance(
        "oci-instance-b", "Synthetic B", "https://b.example.invalid", "oci-install-b"
    )
    state.register_realm("oci-realm-a", "Synthetic realm A", "oci-realm-marker-a")
    state.confirm_identity_binding(
        binding_id="oci-binding-a-b",
        source_realm_key="oci-realm-a",
        source_login="same-login",
        source_external_id="S-1-5-21-OCI-A-1001",
        source_ou_path="OU=People,DC=oci-a,DC=invalid",
        target_instance_key="oci-instance-b",
        target_account_id=4101,
        target_login="mapped-target-user",
        mapping_version=1,
    )
    origin = state.register_origin(
        OriginKey(
            "oci-instance-a",
            "synthetic/selected",
            "issue",
            "9001",
            issue_index=17,
            origin_created_at="2026-11-20T00:00:00Z",
        )
    )
    state.add_object_mapping(
        origin.id,
        "oci-instance-b",
        "synthetic/selected-copy",
        "12001",
        target_issue_index=23,
        target_native_created_at="2026-11-20T00:01:00Z",
    )
    state.create_transfer_round(
        "oci-round-1", "oci-instance-a", "oci-instance-b", "synthetic/selected"
    )
    state.create_baseline(
        baseline_id="oci-baseline-1",
        transfer_round_id="oci-round-1",
        origin_id=origin.id,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline={"title": "Before", "body": "Synthetic"},
        desired={"title": "After", "body": "Synthetic"},
        observed={"title": "Before", "body": "Synthetic"},
        origin_created_at="2026-11-20T00:00:00Z",
        target_native_created_at="2026-11-20T00:01:00Z",
    )
    state.prepare_operation(
        operation_id="oci-operation-unknown",
        origin_id=origin.id,
        target_instance_key="oci-instance-b",
        target_repository_scope="synthetic/selected-copy",
        operation_type="update_issue",
        mapping_version=1,
        baseline_id="oci-baseline-1",
        intent={"title": "After", "body": "Synthetic"},
    )
    state.start_executor_session("oci-executor")
    state.claim_operation("oci-operation-unknown", "oci-executor")
    state.mark_outcome_unknown(
        "oci-operation-unknown", "synthetic remote result requires reconciliation"
    )
    state.end_executor_session("oci-executor")
    PACKAGE_MARKER.write_text("persistent package marker\n", encoding="utf-8")
    WORK_MARKER.write_text("persistent work marker\n", encoding="utf-8")
    return snapshot(state)


def snapshot(state: StateStore) -> dict[str, Any]:
    binding = state.get_identity_binding("oci-binding-a-b")
    origin = state.register_origin(
        OriginKey(
            "oci-instance-a",
            "synthetic/selected",
            "issue",
            "9001",
            issue_index=17,
            origin_created_at="2026-11-20T00:00:00Z",
        )
    )
    mapping = state.get_object_mapping(origin.id, "oci-instance-b")
    baseline = state.get_baseline("oci-baseline-1")
    operation = state.get_operation("oci-operation-unknown")
    evidence = state.operation_evidence(operation.operation_id)
    connection = sqlite3.connect(DATABASE)
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    table_counts = {
        table: int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        )
        for table in (
            "identity_bindings",
            "object_mappings",
            "baselines",
            "operations",
            "operation_events",
            "conflicts",
        )
    }
    connection.close()
    return {
        "binding_id": binding.binding_id,
        "mapping_target_global_id": mapping.target_global_id,
        "baseline": dict(baseline.baseline),
        "operation_status": operation.status,
        "event_transitions": [event["to"] for event in evidence["events"]],
        "open_conflict_types": [
            conflict["type"] for conflict in evidence["conflicts"] if conflict["status"] == "open"
        ],
        "integrity_check": integrity,
        "table_counts": table_counts,
        "package_marker": PACKAGE_MARKER.read_text(encoding="utf-8"),
        "work_marker": WORK_MARKER.read_text(encoding="utf-8"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seed", "verify", "ui", "security"))
    args = parser.parse_args()
    if args.mode == "seed":
        result = seed()
    elif args.mode == "verify":
        result = snapshot(StateStore(DATABASE))
    elif args.mode == "ui":
        result = ui_snapshot()
    else:
        result = admin_security()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
