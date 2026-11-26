"""Actual file-only roundtrip across isolated GitFerry A and B containers."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from gitea_compat import PRIMARY_GITEA_VERSION
from permissions_probe import CONTROL_REPO, SELECTED_REPO
from support import (
    RESULTS,
    SOURCE_URL,
    TARGET_URL,
    USER_PASSWORD,
    ProbeFailure,
    api,
    docker_copy,
    docker_service_exec,
)

EXCHANGE = RESULTS / "transport-exchange"


def worker(service: str, mode: str, *, return_path: str | None = None) -> dict[str, Any]:
    args = ["python", "/probe/transport_worker.py", mode]
    if return_path is not None:
        args = ["env", f"GITFERRY_RETURN_PATH={return_path}", *args]
    completed = docker_service_exec(service, args, check=False)
    if completed.returncode != 0:
        raise ProbeFailure(
            f"transport worker {service}/{mode} failed with {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def worker_expect_blocked(service: str, mode: str) -> dict[str, Any]:
    completed = docker_service_exec(
        service, ["python", "/probe/transport_worker.py", mode], check=False
    )
    if completed.returncode != 5:
        raise ProbeFailure(
            f"transport drift check should have blocked: {completed.returncode} "
            f"{completed.stdout!r} {completed.stderr!r}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def issue_count(base_url: str) -> int:
    return len(api(base_url, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100"))


def copy_between_sides(
    source_service: str, source_path: str, host_path: Path, target_service: str
) -> None:
    shutil.rmtree(host_path, ignore_errors=True)
    docker_copy(f"{source_service}:{source_path}", host_path)
    docker_service_exec(target_service, ["mkdir", "-p", "/state/inbox"])
    docker_copy(host_path, f"{target_service}:/state/inbox/")


def isolated(service: str, forbidden_url: str) -> bool:
    command = (
        "import httpx,sys;\n"
        "try:\n httpx.get(sys.argv[1],timeout=2)\n"
        "except Exception:\n raise SystemExit(0)\n"
        "raise SystemExit(1)"
    )
    return (
        docker_service_exec(
            service, ["python", "-c", command, forbidden_url], check=False
        ).returncode
        == 0
    )


def run_transport_roundtrip_probe() -> dict[str, Any]:
    shutil.rmtree(EXCHANGE, ignore_errors=True)
    EXCHANGE.mkdir(parents=True)

    a_cannot_reach_b = isolated("gitferry-a", "http://target:3000/api/v1/version")
    b_cannot_reach_a = isolated("gitferry-b", "http://source:3000/api/v1/version")
    a_cannot_reach_internet = isolated("gitferry-a", "http://1.1.1.1")
    b_cannot_reach_internet = isolated("gitferry-b", "http://1.1.1.1")
    if not all(
        (
            a_cannot_reach_b,
            b_cannot_reach_a,
            a_cannot_reach_internet,
            b_cannot_reach_internet,
        )
    ):
        raise ProbeFailure("side-local GitFerry worker escaped its isolated importer network")

    seed = worker("gitferry-a", "seed-forward")
    forward_host = EXCHANGE / "forward"
    copy_between_sides("gitferry-a", "/state/outbox/forward", forward_host, "gitferry-b")
    target_before = issue_count(TARGET_URL)
    forward_import = worker("gitferry-b", "import-forward")
    target_after = issue_count(TARGET_URL)
    forward_replay = worker("gitferry-b", "import-forward")
    target_replayed = issue_count(TARGET_URL)

    returned = worker("gitferry-b", "modify-and-return")
    return_host = EXCHANGE / "return"
    copy_between_sides("gitferry-b", "/state/outbox/return", return_host, "gitferry-a")

    control_before = api(SOURCE_URL, "GET", f"/repos/{CONTROL_REPO}")
    api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{CONTROL_REPO}",
        json_body={"description": control_before["description"] + " portable-control-change"},
    )
    original = api(
        SOURCE_URL,
        "GET",
        f"/repos/{SELECTED_REPO}/issues/{seed['issue']['number']}",
        auth=("issue-author", USER_PASSWORD),
    )
    drifted = api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/{original['number']}",
        auth=("issue-author", USER_PASSWORD),
        json_body={
            "body": "Relevant A drift before return",
            "content_version": original["content_version"],
        },
    )
    drift_block = worker_expect_blocked("gitferry-a", "check-return")
    api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/{original['number']}",
        auth=("issue-author", USER_PASSWORD),
        json_body={"body": original["body"], "content_version": drifted["content_version"]},
    )

    time.sleep(2)
    source_before = issue_count(SOURCE_URL)
    applied = worker("gitferry-a", "apply-return")
    source_after = issue_count(SOURCE_URL)
    replay = worker("gitferry-a", "apply-return")
    source_replayed = issue_count(SOURCE_URL)

    repackaged = worker("gitferry-b", "repackage-return")
    repackaged_host = EXCHANGE / "repackaged"
    copy_between_sides("gitferry-b", "/state/outbox/repackaged", repackaged_host, "gitferry-a")
    repackaged_apply = worker("gitferry-a", "apply-return", return_path="/state/inbox/repackaged")
    source_repackaged = issue_count(SOURCE_URL)

    existing_outcome, new_outcome = applied["outcomes"]
    control_after = api(SOURCE_URL, "GET", f"/repos/{CONTROL_REPO}")
    assertions = {
        "separate_networks_block_cross_gitea": a_cannot_reach_b and b_cannot_reach_a,
        "offline_importers_block_external_network": a_cannot_reach_internet
        and b_cannot_reach_internet,
        "forward_created_exactly_one_b_copy": target_after == target_before + 1,
        "same_forward_manifest_restart_created_no_duplicate": target_replayed == target_after,
        "forward_replay_used_same_origin": forward_import["origin_id"]
        == forward_replay["origin_id"],
        "selected_a_drift_blocked_before_write": drift_block["status"] == "blocked",
        "control_repo_change_did_not_block": applied["status"] == "passed",
        "return_updated_existing_and_created_one": source_after == source_before + 1,
        "same_return_manifest_restart_created_no_duplicate": source_replayed == source_after,
        "new_manifest_same_origins_created_no_duplicate": source_repackaged == source_after,
        "existing_a_identity_retained": existing_outcome["target_global_id"]
        == str(seed["issue"]["id"]),
        "changed_content_returned": existing_outcome["values"]["body"]
        == "Changed on B and returned through files",
        "new_b_origin_retained": repackaged["origin_ids"][1] == returned["origin_ids"][1],
        "functional_authors_retained": existing_outcome["author"] == "issue-author"
        and new_outcome["author"] == "issue-author",
        "origin_and_native_time_kept_separate": new_outcome["origin_created_at"]
        != new_outcome["native_created_at"],
        "control_data_preserved": control_after["description"].endswith("portable-control-change"),
        "repackaged_apply_reused_confirmed_operations": all(
            outcome["status"] == "confirmed" for outcome in repackaged_apply["outcomes"]
        ),
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"portable transport assertions failed: {assertions}")

    return {
        "status": "passed",
        "gitea_version": PRIMARY_GITEA_VERSION,
        "state_isolation": {
            "gitferry_a_database": "gitferry-a-state",
            "gitferry_b_database": "gitferry-b-state",
            "shared_state_volume": False,
            "a_cannot_reach_gitea_b": a_cannot_reach_b,
            "b_cannot_reach_gitea_a": b_cannot_reach_a,
            "a_cannot_reach_external_network": a_cannot_reach_internet,
            "b_cannot_reach_external_network": b_cannot_reach_internet,
        },
        "file_exchange": {
            "forward_manifest_id": seed["manifest_id"],
            "forward_digest_sha256": seed["digest_sha256"],
            "return_manifest_id": returned["manifest_id"],
            "return_digest_sha256": returned["digest_sha256"],
            "repackaged_manifest_id": repackaged["manifest_id"],
            "host_exchange_directory": str(EXCHANGE),
        },
        "forward": {"first": forward_import, "replay_after_process_restart": forward_replay},
        "return": {"applied": applied, "replay_after_process_restart": replay},
        "drift": drift_block,
        "assertions": assertions,
        "portable_information": [
            "stable origins and instance-scoped replica facts",
            "selected issue values and explicit comparison facts",
            "realm-qualified identity claims without local approval",
            "forward manifest id plus exact byte digest",
        ],
        "local_only_information": [
            "connection credentials and Gitea authorization",
            "identity approvals and conflict decisions",
            "operations, events, executor leases, and file locks",
        ],
        "limitations": [
            "The v1 manifest contract and executed roundtrip carry issues only.",
            "Native historical creation timestamps are recorded as origin facts but not restored.",
            "SHA-256 proves byte consistency, not authenticated provenance; no signature exists.",
            "No general exactly-once guarantee is claimed across SQLite and Gitea.",
        ],
    }
