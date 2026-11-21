"""Local operator CLI for durable source exports."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from gitferry.connection_probe import ConnectionUnavailable
from gitferry.connection_profiles import ProfileConflict
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.runtime import RuntimePaths
from gitferry.source_exports import (
    GiteaBundleError,
    SourceExportConflict,
    SourceExports,
    SourceSnapshotError,
)
from gitferry.state_store import StateStore, StateStoreError


def configure(parser: argparse.ArgumentParser, database_default: str | None) -> None:
    actions = parser.add_subparsers(dest="export_action", required=True)

    def database(action: argparse.ArgumentParser) -> None:
        action.add_argument(
            "--database", type=Path, default=database_default, required=database_default is None
        )

    prepare = actions.add_parser("prepare")
    database(prepare)
    prepare.add_argument(
        "--source-instance", "--source-instance-key", dest="source_instance_key", required=True
    )
    prepare.add_argument(
        "--source-revision",
        "--source-profile-revision",
        dest="source_profile_revision",
        required=True,
        type=int,
    )
    prepare.add_argument(
        "--recipient-instance",
        "--recipient-instance-key",
        dest="recipient_instance_key",
        required=True,
    )
    prepare.add_argument(
        "--repository",
        "--scope",
        dest="repositories",
        action="append",
        required=True,
        metavar="SCOPE=NATIVE_ID",
        help="Selected repository and its native ID; repeat for each repository",
    )
    prepare.add_argument("--include-metadata", action="store_true")
    prepare.add_argument("--include-members", action="store_true")
    prepare.add_argument("--export-id")

    list_action = actions.add_parser("list")
    database(list_action)
    show = actions.add_parser("show")
    database(show)
    show.add_argument("--export-id", required=True)
    run = actions.add_parser("run")
    database(run)
    run.add_argument("--export-id", required=True)


def _paths(database: Path) -> RuntimePaths:
    paths = replace(RuntimePaths.from_environment(), state_database=database)
    if not paths.package_directory.is_dir() or not paths.work_directory.is_dir():
        raise ValueError("local package and work directories must already exist")
    return paths


def _selection(value: str) -> dict[str, str]:
    """Parse ``scope=native-id`` while preserving native IDs as strings."""
    scope, separator, native_id = value.rpartition("=")
    if not separator or not scope or not native_id:
        raise ValueError("repository scope must be written as scope=native-id")
    return {"repository": scope, "global_id": native_id}


def execute(args: argparse.Namespace) -> int:
    try:
        paths = _paths(args.database)
        service = SourceExports(StateStore(args.database), paths)
        action = args.export_action
        result: dict[str, Any] | list[dict[str, Any]]
        if action == "prepare":
            request: dict[str, Any] = {
                "version": 1,
                "source_instance_key": args.source_instance_key,
                "source_profile_revision": args.source_profile_revision,
                "recipient_instance_key": args.recipient_instance_key,
                "repositories": [_selection(value) for value in args.repositories],
                "include_metadata": args.include_metadata,
                "include_members": args.include_members,
            }
            result = service.prepare(request, export_id=args.export_id)
        elif action == "list":
            result = service.list()
        elif action == "show":
            result = service.get(args.export_id)
        elif action == "run":
            result = service.run(args.export_id)
        else:
            raise ValueError("Unknown source export command")
        print(json.dumps(result, sort_keys=True))
        if action == "run" and isinstance(result, dict) and result["status"] != "captured":
            return 2
        return 0
    except (
        ValueError,
        KeyError,
        SourceExportConflict,
        ProfileConflict,
        ProvisioningTargetConflict,
    ):
        print(
            json.dumps(
                {"status": "failed", "error": "Source export selection is invalid or unavailable"},
                sort_keys=True,
            )
        )
        return 2
    except (SourceSnapshotError, GiteaBundleError):
        print(
            json.dumps(
                {"status": "failed", "error": "Source export execution failed"},
                sort_keys=True,
            )
        )
        return 2
    except (OSError, StateStoreError, ConnectionUnavailable):
        print(
            json.dumps(
                {"status": "failed", "error": "Local source export state unavailable"},
                sort_keys=True,
            )
        )
        return 2


__all__ = ["configure", "execute"]
