"""Exercise the production OCI lifecycle with scoped disposable Docker resources."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from support import COMPOSE_PROJECT, ROOT, ProbeFailure, run

IMAGE = os.environ["GITFERRY_IT_RUNTIME_IMAGE"]
SOURCE_REVISION = os.environ["GITFERRY_SOURCE_REVISION"]
WORKER = ROOT / "tests" / "integration" / "oci_runtime_worker.py"


def docker(args: list[str], *, check: bool = True, timeout: float = 120) -> str:
    result = run(["docker", *args], check=check, timeout=timeout)
    return "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)


def wait_for_health(container: str, expected: str = "healthy", timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = docker(["inspect", "--format", "{{.State.Health.Status}}", container])
        if last == expected:
            return
        if last == "unhealthy" and expected == "healthy":
            break
        time.sleep(0.5)
    logs = docker(["logs", container], check=False)
    raise ProbeFailure(f"container {container} health was {last}, expected {expected}: {logs}")


def wait_for_exit(container: str, timeout: float = 30) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = docker(["inspect", "--format", "{{.State.Status}}", container])
        if state == "exited":
            code = int(docker(["inspect", "--format", "{{.State.ExitCode}}", container]))
            return code, docker(["logs", container], check=False)
        time.sleep(0.25)
    raise ProbeFailure(f"container {container} did not exit within {timeout:.0f}s")


def create_volume(name: str, volumes: list[str]) -> None:
    docker(
        [
            "volume",
            "create",
            "--label",
            f"gitferry.integration.project={COMPOSE_PROJECT}",
            name,
        ]
    )
    volumes.append(name)


def start_runtime(name: str, volume: str, containers: list[str]) -> str:
    container_id = docker(
        [
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"gitferry.integration.project={COMPOSE_PROJECT}",
            "--network",
            "none",
            "-v",
            f"{volume}:/var/lib/gitferry",
            "-v",
            f"{WORKER}:/probe/oci_runtime_worker.py:ro",
            IMAGE,
        ]
    )
    containers.append(name)
    return container_id


def exec_worker(container: str, mode: str) -> dict[str, Any]:
    output = docker(["exec", container, "python", "/probe/oci_runtime_worker.py", mode])
    value = json.loads(output)
    if not isinstance(value, dict):
        raise ProbeFailure("OCI runtime worker did not return an object")
    return value


def prepare_failure_volume(volume: str, script: str) -> None:
    docker(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--label",
            f"gitferry.integration.project={COMPOSE_PROJECT}",
            "-v",
            f"{volume}:/var/lib/gitferry",
            "--entrypoint",
            "python",
            IMAGE,
            "-c",
            script,
        ]
    )


def read_failure_volume(volume: str, script: str) -> str:
    return docker(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--label",
            f"gitferry.integration.project={COMPOSE_PROJECT}",
            "-v",
            f"{volume}:/var/lib/gitferry",
            "--entrypoint",
            "python",
            IMAGE,
            "-c",
            script,
        ]
    )


def assert_failed_start(
    *,
    case: str,
    setup: str,
    expected_log: str,
    containers: list[str],
    volumes: list[str],
    verification: tuple[str, str] | None = None,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    volume = f"{COMPOSE_PROJECT}-oci-{case}"
    container = f"{COMPOSE_PROJECT}-oci-{case}"
    create_volume(volume, volumes)
    prepare_failure_volume(volume, setup)
    command = [
        "run",
        "-d",
        "--name",
        container,
        "--label",
        f"gitferry.integration.project={COMPOSE_PROJECT}",
        "--network",
        "none",
        "--env",
        "GITFERRY_TEST_SECRET=must-not-appear-in-logs",
    ]
    for key, value in sorted((environment or {}).items()):
        command.extend(("--env", f"{key}={value}"))
    command.extend(("-v", f"{volume}:/var/lib/gitferry", IMAGE))
    docker(command)
    containers.append(container)
    exit_code, logs = wait_for_exit(container)
    if exit_code == 0 or expected_log not in logs:
        raise ProbeFailure(
            f"{case} startup did not fail as expected: exit={exit_code}, logs={logs}"
        )
    if "must-not-appear-in-logs" in logs:
        raise ProbeFailure(f"{case} startup leaked an unrelated environment secret")
    preserved = True
    if verification is not None:
        script, expected = verification
        preserved = read_failure_volume(volume, script) == expected
        if not preserved:
            raise ProbeFailure(f"{case} startup changed the rejected state database")
    return {
        "exit_code": exit_code,
        "expected_diagnostic": expected_log in logs,
        "rejected_data_preserved": preserved,
    }


def run_oci_runtime_probe() -> dict[str, Any]:
    containers: list[str] = []
    volumes: list[str] = []
    runtime = f"{COMPOSE_PROJECT}-oci-runtime"
    volume = f"{COMPOSE_PROJECT}-oci-runtime"
    try:
        image = json.loads(docker(["image", "inspect", IMAGE]))[0]
        config = image["Config"]
        labels = config.get("Labels") or {}
        image_contract = {
            "non_root_user": config.get("User") == "10001:10001",
            "version_label": labels.get("org.opencontainers.image.version") == "0.1.0.dev0",
            "revision_label": labels.get("org.opencontainers.image.revision") == SOURCE_REVISION,
            "license_label": labels.get("org.opencontainers.image.licenses") == "Apache-2.0",
            "healthcheck_present": bool(config.get("Healthcheck")),
            "graceful_stop_signal": config.get("StopSignal") == "SIGTERM",
        }
        if not all(image_contract.values()):
            raise ProbeFailure(f"runtime image contract mismatch: {image_contract}")

        create_volume(volume, volumes)
        first_container_id = start_runtime(runtime, volume, containers)
        wait_for_health(runtime)
        packaged_ui = exec_worker(runtime, "ui")
        first_inspect = json.loads(docker(["inspect", runtime]))[0]
        if first_inspect["HostConfig"].get("Privileged"):
            raise ProbeFailure("runtime unexpectedly uses privileged mode")
        if any("docker.sock" in mount.get("Source", "") for mount in first_inspect["Mounts"]):
            raise ProbeFailure("runtime unexpectedly mounts a Docker socket")
        seeded = exec_worker(runtime, "seed")

        docker(["stop", "--timeout", "15", runtime], timeout=30)
        graceful_logs = docker(["logs", runtime], check=False)
        if "Finished server process" not in graceful_logs:
            raise ProbeFailure("uvicorn did not report a graceful server shutdown")
        docker(["rm", runtime])
        containers.remove(runtime)

        second_container_id = start_runtime(runtime, volume, containers)
        wait_for_health(runtime)
        after_replacement = exec_worker(runtime, "verify")
        if seeded != after_replacement or first_container_id == second_container_id:
            raise ProbeFailure("container replacement did not preserve exact durable state")

        docker(["kill", "--signal", "KILL", runtime])
        docker(["start", runtime])
        wait_for_health(runtime)
        after_forced_restart = exec_worker(runtime, "verify")
        if after_forced_restart != seeded:
            raise ProbeFailure("forced restart changed durable state")

        failures = {
            "newer_schema": assert_failed_start(
                case="newer-schema",
                setup=(
                    "import sqlite3; p='/var/lib/gitferry/state/gitferry.sqlite3'; "
                    "c=sqlite3.connect(p); c.execute('CREATE TABLE future_data(value TEXT)'); "
                    "c.execute(\"INSERT INTO future_data VALUES ('keep-me')\"); "
                    "c.execute('PRAGMA user_version=999'); c.commit(); c.close(); "
                    "__import__('pathlib').Path(p).chmod(0o600)"
                ),
                expected_log="newer schema version 999",
                containers=containers,
                volumes=volumes,
                verification=(
                    "import sqlite3; c=sqlite3.connect('/var/lib/gitferry/state/"
                    "gitferry.sqlite3'); print(c.execute('SELECT value FROM future_data')"
                    ".fetchone()[0])",
                    "keep-me",
                ),
            ),
            "corrupt_database": assert_failed_start(
                case="corrupt",
                setup=(
                    "from pathlib import Path; p=Path('/var/lib/gitferry/state/gitferry.sqlite3'); "
                    "p.write_bytes(b'not-a-sqlite-database\\x00keep-this'); p.chmod(0o600)"
                ),
                expected_log="cannot open state database",
                containers=containers,
                volumes=volumes,
                verification=(
                    "from pathlib import Path; print(Path('/var/lib/gitferry/state/"
                    "gitferry.sqlite3').read_bytes().hex())",
                    b"not-a-sqlite-database\x00keep-this".hex(),
                ),
            ),
            "read_only_directory": assert_failed_start(
                case="read-only",
                setup="from pathlib import Path; Path('/var/lib/gitferry/packages').chmod(0o500)",
                expected_log="not writable",
                containers=containers,
                volumes=volumes,
            ),
            "symlink_path": assert_failed_start(
                case="symlink",
                setup=(
                    "from pathlib import Path; root=Path('/var/lib/gitferry/packages'); "
                    "(root/'real').mkdir(); "
                    "(root/'linked').symlink_to(root/'real', target_is_directory=True)"
                ),
                expected_log="symbolic link",
                containers=containers,
                volumes=volumes,
                environment={"GITFERRY_PACKAGE_DIR": "/var/lib/gitferry/packages/linked"},
            ),
        }
        assertions = {
            **image_contract,
            "fresh_volume_initialized": seeded["integrity_check"] == "ok",
            "packaged_frontend_served": all(packaged_ui.values()),
            "container_replacement_preserved_state": after_replacement == seeded,
            "identity_binding_preserved": seeded["binding_id"] == "oci-binding-a-b",
            "object_mapping_preserved": seeded["mapping_target_global_id"] == "12001",
            "baseline_preserved": seeded["baseline"]["title"] == "Before",
            "outcome_unknown_preserved": seeded["operation_status"] == "outcome_unknown",
            "operation_events_preserved": seeded["event_transitions"]
            == ["prepared", "executing", "outcome_unknown"],
            "conflict_preserved": seeded["open_conflict_types"] == ["outcome_unknown"],
            "package_and_work_files_preserved": seeded["package_marker"]
            == "persistent package marker\n"
            and seeded["work_marker"] == "persistent work marker\n",
            "graceful_shutdown_observed": "Finished server process" in graceful_logs,
            "forced_restart_recovered_sqlite": after_forced_restart["integrity_check"] == "ok",
            "newer_schema_failed_closed": failures["newer_schema"]["exit_code"] != 0,
            "corrupt_database_failed_closed": failures["corrupt_database"]["exit_code"] != 0,
            "read_only_directory_failed_closed": failures["read_only_directory"]["exit_code"] != 0,
            "symlink_path_failed_closed": failures["symlink_path"]["exit_code"] != 0,
        }
        if not all(assertions.values()):
            raise ProbeFailure(f"OCI runtime assertions failed: {assertions}")
        return {
            "status": "passed",
            "image_id": image["Id"],
            "first_container_id": first_container_id,
            "replacement_container_id": second_container_id,
            "assertions": assertions,
            "failure_cases": failures,
            "state": seeded,
            "packaged_frontend": packaged_ui,
        }
    finally:
        for container in reversed(containers):
            docker(["rm", "--force", container], check=False)
        for owned_volume in reversed(volumes):
            docker(["volume", "rm", "--force", owned_volume], check=False)


if __name__ == "__main__":
    json.dump(run_oci_runtime_probe(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
