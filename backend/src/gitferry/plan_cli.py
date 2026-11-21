"""Local operator CLI for durable transfer plans."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.operations import ExecutorBusy
from gitferry.plan_application import prepare_transfer_plan
from gitferry.plan_runner import PlanRunner
from gitferry.profile_api import MAX_CONFIGURATION_BYTES, parse_configuration
from gitferry.runtime import RuntimePaths
from gitferry.state_store import OperationBlocked, StateStore, StateStoreError
from gitferry.transfer_plans import PlanConflict, PlanService


def configure(parser: argparse.ArgumentParser, database_default: str | None) -> None:
    actions = parser.add_subparsers(dest="plan_action", required=True)

    def database(action: argparse.ArgumentParser) -> None:
        action.add_argument(
            "--database", type=Path, default=database_default, required=database_default is None
        )

    prepare = actions.add_parser("prepare")
    database(prepare)
    prepare.add_argument("--input", type=Path, required=True)
    list_action = actions.add_parser("list")
    database(list_action)
    show = actions.add_parser("show")
    database(show)
    show.add_argument("--plan-id", required=True)
    queue = actions.add_parser("queue")
    database(queue)
    queue.add_argument("--plan-id", required=True)
    queue.add_argument("--digest", required=True)
    queue.add_argument("--preview-generation", type=int)
    preview = actions.add_parser("preview")
    database(preview)
    preview.add_argument("--plan-id", required=True)
    preview.add_argument("--digest", required=True)
    cancel = actions.add_parser("cancel")
    database(cancel)
    cancel.add_argument("--plan-id", required=True)
    run = actions.add_parser("run")
    database(run)
    run.add_argument("--plan-id")


def _input(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(MAX_CONFIGURATION_BYTES + 1)
    return parse_configuration(data)


def _paths(database: Path) -> RuntimePaths:
    paths = replace(RuntimePaths.from_environment(), state_database=database)
    if not paths.package_directory.is_dir() or not paths.work_directory.is_dir():
        raise ValueError("local package and work directories must already exist")
    return paths


def execute(args: argparse.Namespace) -> int:
    try:
        state = StateStore(args.database)
        service = PlanService(state)
        action = args.plan_action
        result: dict[str, Any] | list[dict[str, Any]] | None
        if action == "prepare":
            result = prepare_transfer_plan(state, _input(args.input))
        elif action == "list":
            result = service.list()
        elif action == "show":
            result = service.get(args.plan_id)
        elif action == "queue":
            result = service.queue(
                args.plan_id, args.digest, preview_generation=args.preview_generation
            )
        elif action == "preview":
            paths = _paths(args.database)
            service.request_preview(args.plan_id, args.digest)
            result = PlanRunner(service, lambda _: GiteaPlanAdapter(state, paths)).preview(
                args.plan_id
            )
        elif action == "cancel":
            result = service.cancel(args.plan_id)
        elif action == "run":
            paths = _paths(args.database)
            runner = PlanRunner(service, lambda _: GiteaPlanAdapter(state, paths))
            result = runner.run(args.plan_id) if args.plan_id else runner.run_next()
        else:
            raise ValueError("Unknown plan command")
        print(json.dumps(result, sort_keys=True))
        if action == "run" and isinstance(result, dict) and result["status"] != "confirmed":
            return 2
        if (
            action == "preview"
            and isinstance(result, dict)
            and result["preview"]["status"] != "passed"
        ):
            return 2
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        PlanConflict,
        OperationBlocked,
        StateStoreError,
        ExecutorBusy,
        ProvisioningTargetConflict,
    ) as exc:
        message = (
            "Transfer plan selection is invalid or unavailable"
            if isinstance(exc, (ValueError, PlanConflict, OperationBlocked))
            else "Local plan state unavailable"
        )
        print(json.dumps({"status": "failed", "error": message}, sort_keys=True))
        return 2


__all__ = ["configure", "execute"]
