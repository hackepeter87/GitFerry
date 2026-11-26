"""Bounded cross-version Gitea compatibility probe (1.27.2 -> 28.4.0).

This deliberately exercises only the documented repository migration API.  It
is separate from the primary integration report because the normal test matrix
uses one reproducible Gitea baseline on both sides.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from gitea_compat import (
    HISTORICAL_GITEA_IMAGE,
    HISTORICAL_GITEA_VERSION,
    PRIMARY_GITEA_IMAGE,
    PRIMARY_GITEA_VERSION,
)

ROOT = Path(__file__).parents[2]
COMPOSE_FILE = ROOT / "tests" / "integration" / "compose.yaml"


def run(
    command: list[str],
    *,
    check: bool = True,
    timeout: float = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def compose(
    project: str, *args: str, check: bool = True, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args],
        check=check,
        timeout=timeout,
    )


def wait_healthy(project: str, service: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container = compose(project, "ps", "-q", service, check=False).stdout.strip()
        if container:
            state = run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                    container,
                ],
                check=False,
            ).stdout.strip()
            if state == "healthy":
                return
        time.sleep(1)
    raise RuntimeError(f"{service} did not become healthy")


def api(
    base: str, method: str, path: str, auth: tuple[str, str], body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    response = httpx.request(method, f"{base}/api/v1{path}", auth=auth, json=body, timeout=30)
    try:
        value: Any = response.json() if response.content else None
    except ValueError:
        value = response.text[:200]
    return response.status_code, value


def runtime_evidence(project: str, service: str) -> dict[str, Any]:
    """Record the configured index and the platform manifest Docker resolved."""

    container = compose(project, "ps", "-q", service).stdout.strip()
    image_id = run(["docker", "inspect", "--format", "{{.Image}}", container]).stdout.strip()
    configured = run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container]
    ).stdout.strip()
    image = json.loads(run(["docker", "image", "inspect", image_id]).stdout)[0]
    raw_index = json.loads(
        run(["docker", "buildx", "imagetools", "inspect", "--raw", configured]).stdout
    )
    platform_key = f"{image.get('Os')}/{image.get('Architecture')}"
    resolved_digest = None
    for manifest in raw_index.get("manifests", []):
        manifest_platform = manifest.get("platform", {})
        key = f"{manifest_platform.get('os')}/{manifest_platform.get('architecture')}"
        if key == platform_key:
            resolved_digest = manifest.get("digest")
            break
    if not isinstance(resolved_digest, str):
        raise RuntimeError(f"no platform manifest found for {platform_key}")
    return {
        "configured_image": configured,
        "image_id": image_id,
        "os": image.get("Os"),
        "architecture": image.get("Architecture"),
        "repo_digests": image.get("RepoDigests", []),
        "resolved_platform_digest": resolved_digest,
    }


def create_admin(project: str, service: str, password: str) -> None:
    result = compose(
        project,
        "exec",
        "-T",
        "--user",
        "git",
        service,
        "gitea",
        "admin",
        "user",
        "create",
        "--username",
        "gitferry-admin",
        "--password",
        password,
        "--email",
        "gitferry-admin@example.invalid",
        "--admin",
        "--must-change-password=false",
        check=False,
    )
    if result.returncode and "already exists" not in result.stdout.lower() + result.stderr.lower():
        raise RuntimeError(f"admin bootstrap failed for {service}")


def main() -> int:
    run_id = os.environ.get("GITFERRY_UPGRADE_RUN_ID", uuid.uuid4().hex[:12])
    safe = "".join(c if c.isalnum() or c in "_-" else "-" for c in run_id)[:24]
    project = f"gitferry-it-upgrade-{safe}"
    source_port = os.environ.get("GITFERRY_UPGRADE_SOURCE_PORT", "33101")
    target_port = os.environ.get("GITFERRY_UPGRADE_TARGET_PORT", "33102")
    source_url = f"http://127.0.0.1:{source_port}"
    target_url = f"http://127.0.0.1:{target_port}"
    password = secrets.token_urlsafe(24)
    report: dict[str, Any] = {
        "status": "failed",
        "project": project,
        "source": {"version": HISTORICAL_GITEA_VERSION, "image": HISTORICAL_GITEA_IMAGE},
        "target": {"version": PRIMARY_GITEA_VERSION, "image": PRIMARY_GITEA_IMAGE},
        "migration": {"status": "not_started"},
        "cleanup": {"status": "not_started"},
    }
    result_path = Path(
        os.environ.get(
            "GITFERRY_UPGRADE_REPORT",
            str(ROOT / "integration-results" / "gitea-upgrade-report.json"),
        )
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "GITFERRY_IT_SOURCE_GITEA_IMAGE": HISTORICAL_GITEA_IMAGE,
            "GITFERRY_IT_TARGET_GITEA_IMAGE": PRIMARY_GITEA_IMAGE,
            "GITFERRY_IT_SOURCE_PORT": source_port,
            "GITFERRY_IT_TARGET_PORT": target_port,
            "GITFERRY_IT_PROJECT": project,
        }
    )
    try:
        server = json.loads(run(["docker", "version", "--format", "{{json .Server}}"]).stdout)
        report["code_state"] = {
            "git_head": run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip(),
            "dirty": bool(
                run(
                    ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all"]
                ).stdout.strip()
            ),
        }
        report["platform"] = {
            "host_system": platform.system(),
            "host_architecture": platform.machine(),
            "python": platform.python_version(),
            "docker_server_os": server.get("Os"),
            "docker_server_architecture": server.get("Arch"),
            "docker_server_version": server.get("Version"),
        }
        run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(COMPOSE_FILE),
                "up",
                "-d",
                "source",
                "target",
            ],
            timeout=180,
            env=env,
        )
        wait_healthy(project, "source")
        wait_healthy(project, "target")
        report["source"]["runtime"] = runtime_evidence(project, "source")
        report["target"]["runtime"] = runtime_evidence(project, "target")
        source_version_status, source_version = api(source_url, "GET", "/version", ("", ""))
        target_version_status, target_version = api(target_url, "GET", "/version", ("", ""))
        source_observed = (
            source_version.get("version") if isinstance(source_version, dict) else None
        )
        target_observed = (
            target_version.get("version") if isinstance(target_version, dict) else None
        )
        report["source"]["observed_version"] = source_observed
        report["target"]["observed_version"] = target_observed
        if source_version_status != 200 or source_observed != HISTORICAL_GITEA_VERSION:
            raise RuntimeError(f"unexpected source version: {source_observed!r}")
        if target_version_status != 200 or target_observed != PRIMARY_GITEA_VERSION:
            raise RuntimeError(f"unexpected target version: {target_observed!r}")
        create_admin(project, "source", password)
        create_admin(project, "target", password)
        auth = ("gitferry-admin", password)
        source_repo = "upgrade-compat-source"
        target_repo = "upgrade-compat-imported"
        status, _ = api(
            source_url,
            "POST",
            "/user/repos",
            auth,
            {"name": source_repo, "private": True, "auto_init": True},
        )
        if status not in (201, 422):
            raise RuntimeError(f"source repository creation returned {status}")
        status, issue = api(
            source_url,
            "POST",
            f"/repos/gitferry-admin/{source_repo}/issues",
            auth,
            {"title": "Cross-version issue", "body": "Synthetic 1.27.2 source data"},
        )
        if status != 201 or not isinstance(issue, dict):
            raise RuntimeError(f"source issue creation returned {status}")
        migration_body = {
            "clone_addr": f"http://source:3000/gitferry-admin/{source_repo}.git",
            "auth_username": "gitferry-admin",
            "auth_password": password,
            "service": "gitea",
            "issues": True,
            "labels": True,
            "private": True,
            "repo_owner": "gitferry-admin",
            "repo_name": target_repo,
        }
        status, migrated = api(target_url, "POST", "/repos/migrate", auth, migration_body)
        report["migration"].update(
            {"post_status": status, "source_issue_number": issue.get("number")}
        )
        if status not in (201, 202) or not isinstance(migrated, dict):
            raise RuntimeError(f"cross-version migration returned {status}")
        for _ in range(45):
            status, issues = api(
                target_url,
                "GET",
                f"/repos/gitferry-admin/{target_repo}/issues?state=all&limit=50",
                auth,
            )
            if (
                status == 200
                and isinstance(issues, list)
                and any(
                    item.get("title") == issue.get("title")
                    for item in issues
                    if isinstance(item, dict)
                )
            ):
                report["migration"].update(
                    {
                        "status": "passed",
                        "target_repository": target_repo,
                        "target_issue_present": True,
                    }
                )
                report["status"] = "passed"
                break
            time.sleep(1)
        if report["status"] != "passed":
            raise RuntimeError("migrated issue was not visible on target")
    except Exception as exc:
        report["error"] = type(exc).__name__
    finally:
        cleanup = compose(
            project, "down", "--volumes", "--remove-orphans", check=False, timeout=180
        )
        remaining_containers = run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            check=False,
        ).stdout.split()
        remaining_volumes = run(
            [
                "docker",
                "volume",
                "ls",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            check=False,
        ).stdout.split()
        cleanup_passed = (
            cleanup.returncode == 0 and not remaining_containers and not remaining_volumes
        )
        report["cleanup"] = {
            "status": "passed" if cleanup_passed else "failed",
            "compose_exit_code": cleanup.returncode,
            "remaining_containers": len(remaining_containers),
            "remaining_volumes": len(remaining_volumes),
        }
        if not cleanup_passed:
            report["status"] = "failed"
            report["error"] = "CleanupError"
        result_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
