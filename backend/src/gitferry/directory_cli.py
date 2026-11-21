"""Safe host-local directory settings and bootstrap CLI."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from gitferry.blueprints import load_and_validate
from gitferry.connection_profiles import ProfileService
from gitferry.directory_execution import DirectoryBootstrapService, DirectoryBootstrapUnknown
from gitferry.directory_local import LocalBootstrap
from gitferry.directory_settings import DirectorySettingsService
from gitferry.state_store import StateStore

_MAX_INPUT = 1024 * 1024


def configure(parser: argparse.ArgumentParser, database_default: str | None) -> None:
    parser.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    actions = parser.add_subparsers(dest="directory_action", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--instance")
    save = actions.add_parser("save")
    save.add_argument("--instance", required=True)
    save.add_argument("--input", type=Path, required=True)
    save.add_argument("--expected-revision", type=int)
    imported = actions.add_parser("import")
    imported.add_argument("--instance", required=True)
    imported.add_argument("--input", type=Path, required=True)
    imported.add_argument("--expected-revision", type=int)
    exported = actions.add_parser("export")
    exported.add_argument("--instance", required=True)
    exported.add_argument("--source-name", required=True)
    inspect = actions.add_parser("inspect-bootstrap")
    inspect.add_argument("--instance", required=True)
    inspect.add_argument("--source-name", required=True)
    for action_name in ("bootstrap-plan", "bootstrap-apply", "bootstrap-status"):
        action = actions.add_parser(action_name)
        action.add_argument("--instance", required=True)
        action.add_argument("--source-name", required=True)
        action.add_argument("--gitea-binary", type=Path, required=True)
        action.add_argument("--gitea-config", type=Path, required=True)
        action.add_argument("--ca-file", type=Path, required=True)
        action.add_argument("--work-path", type=Path, required=True)
        action.add_argument("--custom-path", type=Path, required=True)


def _input(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_INPUT + 1)
    if len(raw) > _MAX_INPUT:
        raise ValueError("directory input is too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("directory input must be an object")
    return value


def _blueprint_input(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_INPUT:
        raise ValueError("blueprint input is too large")
    return load_and_validate(path)


def _bootstrap(
    state: StateStore, args: argparse.Namespace, settings: DirectorySettingsService
) -> LocalBootstrap:
    record = settings.get(args.instance, args.source_name)
    profile = ProfileService(state).get(args.instance)
    if (
        not profile.enabled
        or not profile.locally_approved
        or record["profile_revision"] != profile.revision
    ):
        raise ValueError("directory setting profile revision is stale or not approved")
    return LocalBootstrap(
        state,
        args.instance,
        record["realm_key"],
        record["source"],
        args.gitea_binary,
        args.gitea_config,
        args.ca_file,
        work_path=args.work_path,
        custom_path=args.custom_path,
    )


def _safe_error() -> int:
    print(
        json.dumps(
            {
                "status": "failed",
                "error": (
                    "Directory operation invalid, conflicted or unavailable; inspect local state."
                ),
            }
        )
    )
    return 2


def execute(args: argparse.Namespace) -> int:
    try:
        state = StateStore(args.database)
        settings = DirectorySettingsService(state)
        action = args.directory_action
        result: Any
        if action == "list":
            result = settings.list(args.instance)
        elif action == "save":
            result = settings.save(args.instance, _input(args.input), args.expected_revision)
        elif action == "import":
            result = settings.import_blueprint(
                args.instance, _blueprint_input(args.input), args.expected_revision
            )
        elif action == "inspect-bootstrap":
            journal = DirectoryBootstrapService(state)
            record = journal.get(args.instance, args.source_name)
            result = {
                "record": asdict(record) if record else None,
                "events": journal.events(args.instance, args.source_name),
            }
        elif action == "export":
            result = settings.export(args.instance, args.source_name)
        else:
            bootstrap = _bootstrap(state, args, settings)
            if action == "bootstrap-plan":
                plan = bootstrap.plan()
                result = {
                    "status": "ready-for-target-local-bootstrap",
                    "digest": plan.preflight.intent_digest,
                    "prerequisites": settings.get(args.instance, args.source_name)["prerequisites"],
                }
            elif action == "bootstrap-apply":
                try:
                    record = bootstrap.apply()
                except DirectoryBootstrapUnknown:
                    record = DirectoryBootstrapService(state).get(args.instance, args.source_name)
                    if record is None:
                        raise
                    result = {
                        "status": "outcome_unknown",
                        "native_source_id": record.native_source_id,
                        "verification_scope": (
                            "inspect local Gitea state; identity-name-type-active only"
                        ),
                    }
                    print(json.dumps(result, sort_keys=True))
                    return 2
                result = {
                    "status": record.status,
                    "native_source_id": record.native_source_id,
                    "verification_scope": (
                        "identity-name-type-active only; full configuration/login not verified"
                    ),
                }
            else:
                record = bootstrap.status()
                result = (
                    None
                    if record is None
                    else {
                        "status": record.status,
                        "native_source_id": record.native_source_id,
                        "verification_scope": (
                            "identity-name-type-active only; full configuration/login not verified"
                        ),
                    }
                )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        return _safe_error()


__all__ = ["configure", "execute"]
