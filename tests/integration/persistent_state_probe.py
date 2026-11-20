"""Orchestrate real-process restart and interruption proofs against Gitea."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from permissions_probe import CONTROL_REPO, SELECTED_REPO, AccessContext
from support import RESULTS, ROOT, SOURCE_URL, TARGET_URL, ProbeFailure, api, run

STATE_DIR = RESULTS / "state"
DATABASE = STATE_DIR / "gitferry.sqlite3"
WORKER = ROOT / "tests" / "integration" / "state_worker.py"
PYTHON = ROOT / ".venv" / "bin" / "python"


def worker_arguments(mode: str, operation_id: str, issue: dict[str, Any]) -> list[str]:
    return [
        str(PYTHON),
        str(WORKER),
        mode,
        "--database",
        str(DATABASE),
        "--operation-id",
        operation_id,
        "--origin-global-id",
        str(issue["id"]),
        "--origin-issue-index",
        str(issue["number"]),
    ]


def run_worker(
    mode: str,
    operation_id: str,
    issue: dict[str, Any],
    *,
    extra: list[str] | None = None,
    expected_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    completed = run([*worker_arguments(mode, operation_id, issue), *(extra or [])], check=False)
    if completed.returncode not in expected_codes:
        raise ProbeFailure(
            f"state worker {mode} returned {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    lines = completed.stdout.strip().splitlines()
    if not lines:
        return {}
    return json.loads(lines[-1])


def create_source_issue(title: str) -> dict[str, Any]:
    return api(
        SOURCE_URL,
        "POST",
        f"/repos/{SELECTED_REPO}/issues",
        auth=("issue-author", os.environ["GITFERRY_IT_USER_PASSWORD"]),
        json_body={"title": title, "body": "Synthetic source for durable state"},
    )


def issue_count(base_url: str) -> int:
    return len(api(base_url, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100"))


def remote_acceptance(operation_id: str) -> dict[str, Any]:
    path = STATE_DIR / f"{operation_id}.remote-accepted.json"
    deadline = time.monotonic() + 5
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not path.is_file():
        raise ProbeFailure(f"missing remote acceptance synchronization point for {operation_id}")
    value = json.loads(path.read_text(encoding="utf-8"))
    target = api(
        TARGET_URL,
        "GET",
        f"/repos/{SELECTED_REPO}/issues/{value['target_issue_index']}",
        auth=("issue-author", os.environ["GITFERRY_IT_USER_PASSWORD"]),
    )
    if (
        str(target["id"]) != value["target_global_id"]
        or target["title"] != value["title"]
        or target["body"] != value["body"]
        or target["user"]["login"] != value["author"]
    ):
        raise ProbeFailure(f"remote acceptance evidence did not match live Gitea: {operation_id}")
    return {**value, "live_issue_verified_after_restart": True}


def run_persistent_state_probe(context: AccessContext) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", ".executor.lock"):
        DATABASE.with_name(DATABASE.name + suffix).unlink(missing_ok=True)

    confirmed_source = create_source_issue("Durable confirmed operation source")
    seed = run_worker(
        "seed-contracts",
        "unused-seed-operation",
        confirmed_source,
        extra=["--target-account-id", str(context.target_users["issue-author"]["id"])],
    )

    control = api(SOURCE_URL, "GET", f"/repos/{CONTROL_REPO}")
    api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{CONTROL_REPO}",
        json_body={"description": control["description"] + " durable-control-change"},
    )
    before_drift = run_worker("verify-contracts", "unused-verify-operation", confirmed_source)
    if before_drift["drift"]:
        raise ProbeFailure("control repository change incorrectly changed selected baseline")

    confirmed_count_before = issue_count(TARGET_URL)
    first = run_worker("normal", "op-confirmed-restart", confirmed_source)
    confirmed_count_after = issue_count(TARGET_URL)
    replay = run_worker("normal", "op-confirmed-restart", confirmed_source)
    confirmed_count_replayed = issue_count(TARGET_URL)

    changed = api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/{confirmed_source['number']}",
        json_body={
            "body": "Relevant central drift after the persisted baseline",
            "content_version": confirmed_source["content_version"],
        },
    )
    after_drift = run_worker("verify-contracts", "unused-drift-operation", changed)
    after_reread = run_worker("verify-contracts", "unused-reread-operation", changed)

    crash_source = create_source_issue("Crash window operation source")
    crash_count_before = issue_count(TARGET_URL)
    crashed = run(worker_arguments("crash", "op-crash-window", crash_source), check=False)
    if crashed.returncode != 91:
        raise ProbeFailure(
            f"crash worker did not exit at failure point: {crashed.returncode} {crashed.stderr}"
        )
    crash_count_after = issue_count(TARGET_URL)
    crash_acceptance = remote_acceptance("op-crash-window")
    recovered = run_worker("status", "op-crash-window", crash_source)
    blocked_retry = run_worker("normal", "op-crash-window", crash_source, expected_codes=(3,))
    crash_count_retried = issue_count(TARGET_URL)

    lost_source = create_source_issue("Lost response operation source")
    lost_count_before = issue_count(TARGET_URL)
    lost = run_worker("lost", "op-lost-response", lost_source, expected_codes=(3,))
    lost_count_after = issue_count(TARGET_URL)
    lost_acceptance = remote_acceptance("op-lost-response")
    lost_retry = run_worker("normal", "op-lost-response", lost_source, expected_codes=(3,))
    lost_count_retried = issue_count(TARGET_URL)

    concurrent_source = create_source_issue("Concurrent executor operation source")
    ready_file = STATE_DIR / "concurrent.ready"
    ready_file.unlink(missing_ok=True)
    first_process = subprocess.Popen(
        [
            *worker_arguments("normal", "op-concurrent", concurrent_source),
            "--executor-id",
            "concurrent-one",
            "--ready-file",
            str(ready_file),
            "--hold-seconds",
            "1.5",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while not ready_file.exists() and first_process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not ready_file.exists():
        first_process.kill()
        raise ProbeFailure("first concurrent worker did not reach the guarded remote call")
    concurrent_count_before = issue_count(TARGET_URL)
    second = run_worker(
        "normal",
        "op-concurrent",
        concurrent_source,
        extra=["--executor-id", "concurrent-two"],
        expected_codes=(4,),
    )
    first_stdout, first_stderr = first_process.communicate(timeout=10)
    if first_process.returncode != 0:
        raise ProbeFailure(
            "first concurrent worker failed: "
            f"{first_process.returncode} {first_stdout} {first_stderr}"
        )
    concurrent_count_after = issue_count(TARGET_URL)

    assertions = {
        "identity_and_baseline_survived_process_restart": before_drift["status"] == "passed",
        "same_origin_mapping_survived_restart": first["target_global_id"]
        == replay["target_global_id"],
        "confirmed_replay_created_no_duplicate": confirmed_count_after == confirmed_count_before + 1
        and confirmed_count_replayed == confirmed_count_after,
        "selected_drift_survived_restart": after_drift["drift"] and after_reread["drift"],
        "reread_did_not_replace_baseline": after_drift["baseline"]
        == before_drift["baseline"]
        == after_reread["baseline"],
        "crash_created_one_remote_issue": crash_count_after == crash_count_before + 1,
        "crash_recovered_as_unknown": recovered["status"] == "outcome_unknown",
        "crash_retry_created_no_duplicate": blocked_retry["status"] == "outcome_unknown"
        and crash_count_retried == crash_count_after,
        "lost_response_created_one_remote_issue": lost_count_after == lost_count_before + 1,
        "lost_response_retry_created_no_duplicate": lost["status"] == "outcome_unknown"
        and lost_retry["status"] == "outcome_unknown"
        and lost_count_retried == lost_count_after,
        "concurrent_executor_was_blocked": second["status"] == "blocked",
        "concurrent_attempts_created_one_issue": concurrent_count_after
        == concurrent_count_before + 1,
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"persistent state assertions failed: {assertions}")

    return {
        "status": "passed",
        "database": str(DATABASE.relative_to(ROOT)),
        "sqlite": {
            "journal_mode": "WAL",
            "synchronous": "FULL",
            "foreign_keys_per_connection": True,
        },
        "seed": seed,
        "confirmed_restart": {"first": first, "replay": replay},
        "crash_window": {
            "remote_acceptance": crash_acceptance,
            "after_restart": recovered,
            "blocked_retry": blocked_retry,
        },
        "lost_response": {
            "remote_acceptance": lost_acceptance,
            "first": lost,
            "blocked_retry": lost_retry,
        },
        "drift_after_restart": {
            "before_selected_change": before_drift,
            "after_selected_change": after_drift,
            "after_reread": after_reread,
        },
        "concurrent_executor": second,
        "assertions": assertions,
        "limitations": [
            "No general exactly-once guarantee is claimed across SQLite and Gitea.",
            "Unknown creates remain halted until a stable target id and exact intent are verified.",
            "The single-writer lock applies to writers sharing this local state volume.",
        ],
    }
