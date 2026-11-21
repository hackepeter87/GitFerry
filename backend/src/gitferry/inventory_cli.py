"""Inventory CLI over the same durable jobs and facts as the desktop API."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from gitferry.content_inventory import CONTENT_CLASSES
from gitferry.inventory_jobs import InventoryJobs
from gitferry.source_export_cli import _selection
from gitferry.state_store import StateStore


def configure(parser: argparse.ArgumentParser, database_default: str | None) -> None:
    actions = parser.add_subparsers(dest="inventory_action", required=True)
    for name in ("create", "list", "show", "document", "run-next"):
        action = actions.add_parser(name)
        action.add_argument(
            "--database", type=Path, default=database_default, required=database_default is None
        )
        if name == "create":
            action.add_argument("--inventory-id")
            action.add_argument("--source-instance", required=True)
            action.add_argument("--source-revision", required=True, type=int)
            action.add_argument(
                "--repository",
                dest="repositories",
                action="append",
                required=True,
                metavar="SCOPE=NATIVE_ID",
            )
            action.add_argument(
                "--class", dest="classes", action="append", required=True, choices=CONTENT_CLASSES
            )
        elif name in ("show", "document"):
            action.add_argument("--inventory-id", required=True)


def execute(args: argparse.Namespace) -> int:
    try:
        jobs = InventoryJobs(StateStore(args.database))
        result: Any
        if args.inventory_action == "create":
            result = jobs.create(
                args.inventory_id or "inventory-" + uuid.uuid4().hex,
                {
                    "source_instance_key": args.source_instance,
                    "source_profile_revision": args.source_revision,
                    "repositories": [_selection(value) for value in args.repositories],
                    "requested_classes": args.classes,
                },
            )
        elif args.inventory_action == "list":
            result = jobs.list()
        elif args.inventory_action == "run-next":
            result = jobs.run_next() or {"status": "idle"}
        else:
            result = jobs.get(args.inventory_id)
            if args.inventory_action == "document":
                if result["document"] is None:
                    raise ValueError("unconfirmed inventory")
                result = result["document"]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": (
                        "Inventory unavailable; check the local source approval, "
                        "selected scope and stored job status"
                    ),
                }
            )
        )
        return 2
