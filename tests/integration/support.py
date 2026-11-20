"""Shared helpers for isolated Gitea integration probes."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).parents[2]
RESULTS = ROOT / "integration-results"
PACKAGE = RESULTS / "package"
WORK = RESULTS / "work"
SOURCE_URL = os.environ.get("GITFERRY_IT_SOURCE_URL", "http://127.0.0.1:33001")
TARGET_URL = os.environ.get("GITFERRY_IT_TARGET_URL", "http://127.0.0.1:33002")
ADMIN_USERNAME = "gitferry-admin"
ADMIN_PASSWORD = os.environ["GITFERRY_IT_ADMIN_PASSWORD"]
USER_PASSWORD = os.environ["GITFERRY_IT_USER_PASSWORD"]
ADMIN = (ADMIN_USERNAME, ADMIN_PASSWORD)
COMPOSE_PROJECT = os.environ.get("GITFERRY_IT_PROJECT", "gitferry-it-verification")
if not COMPOSE_PROJECT.startswith("gitferry-it-"):
    raise RuntimeError("integration Compose project must start with gitferry-it-")
COMPOSE = [
    "docker",
    "compose",
    "-p",
    COMPOSE_PROJECT,
    "-f",
    str(ROOT / "tests" / "integration" / "compose.yaml"),
]


class ProbeFailure(RuntimeError):
    pass


def run(
    args: Sequence[str],
    cwd: Path | None = None,
    check: bool = True,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, cwd=cwd, check=check, text=True, capture_output=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeFailure(f"command exceeded {timeout:.0f}s: {args[0]}") from exc


def request(
    base_url: str,
    method: str,
    path: str,
    *,
    auth: tuple[str, str] = ADMIN,
    json_body: Mapping[str, Any] | None = None,
    timeout: float = 15,
) -> httpx.Response:
    return httpx.request(
        method,
        f"{base_url}/api/v1{path}",
        auth=auth,
        json=json_body,
        timeout=timeout,
    )


def api(
    base_url: str,
    method: str,
    path: str,
    *,
    auth: tuple[str, str] = ADMIN,
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    response = request(base_url, method, path, auth=auth, json_body=json_body)
    if response.status_code >= 400:
        raise ProbeFailure(f"{method} {path} returned {response.status_code}: {response.text}")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def create_user(base_url: str, username: str) -> dict[str, Any]:
    return api(
        base_url,
        "POST",
        "/admin/users",
        json_body={
            "username": username,
            "email": f"{username}@example.invalid",
            "password": USER_PASSWORD,
            "must_change_password": False,
            "send_notify": False,
        },
    )


def authenticated_url(base_url: str, path: str, auth: tuple[str, str] = ADMIN) -> str:
    scheme, rest = base_url.split("://", 1)
    return f"{scheme}://{auth[0]}:{auth[1]}@{rest}{path}"


def docker_exec(
    args: Sequence[str], check: bool = True, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return run([*COMPOSE, "exec", "-T", "importer", *args], check=check, timeout=timeout)


def docker_service_exec(
    service: str, args: Sequence[str], check: bool = True, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return run([*COMPOSE, "exec", "-T", service, *args], check=check, timeout=timeout)


def docker_copy(source: str | Path, destination: str | Path) -> None:
    if isinstance(destination, Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
    run([*COMPOSE, "cp", str(source), str(destination)])
