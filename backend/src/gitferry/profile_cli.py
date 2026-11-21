"""Local administrator CLI for the same connection services used by REST."""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from gitferry.connection_probe import ConnectionUnavailable, approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileConflict, ProfileService
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.profile_api import MAX_CONFIGURATION_BYTES, parse_configuration
from gitferry.source_catalog import browse_source
from gitferry.state_store import StateStore, StateStoreError


def configure(parser: argparse.ArgumentParser, database_default: str | None) -> None:
    parser.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    actions = parser.add_subparsers(dest="profile_action", required=True)
    actions.add_parser("list")
    actions.add_parser("export")
    importer = actions.add_parser("import")
    importer.add_argument("path", type=Path)
    create = actions.add_parser("create")
    create.add_argument("--base-url", required=True)
    create.add_argument("--installation-marker", required=True)
    update = actions.add_parser("update")
    for command in (create, update):
        command.add_argument("--display-name", required=True)
        command.add_argument("--credential-ref", required=True)
        command.add_argument("--ca-ref")
    retire = actions.add_parser("retire")
    rebuild = actions.add_parser("replace-installation")
    rebuild.add_argument("--installation-marker", required=True)
    test = actions.add_parser("test")
    approve = actions.add_parser("approve")
    approve.add_argument("--evidence-digest", required=True)
    browse = actions.add_parser("browse")
    browse.add_argument("--kind", choices=("organizations", "repositories"), required=True)
    browse.add_argument("--organization")
    browse.add_argument("--page", type=int, default=1)
    for command in (update, retire, rebuild, test, approve, browse):
        command.add_argument("instance_key")
        command.add_argument("--expected-revision", required=True, type=int)


def execute(args: argparse.Namespace) -> int:
    try:
        service = ProfileService(StateStore(args.database))
        result: Any
        action = args.profile_action
        if action == "list":
            result = [asdict(profile) for profile in service.list_profiles()]
        elif action == "export":
            result = service.export_configuration()
        elif action == "import":
            with args.path.open("rb") as stream:
                document = parse_configuration(stream.read(MAX_CONFIGURATION_BYTES + 1))
            result = [asdict(profile) for profile in service.import_configuration(document)]
        elif action == "create":
            result = asdict(
                service.create(
                    ConnectionProfile(
                        str(uuid.uuid4()),
                        args.display_name,
                        args.base_url,
                        args.installation_marker,
                        args.credential_ref,
                        args.ca_ref,
                    )
                )
            )
        elif action == "update":
            result = asdict(
                service.update(
                    args.instance_key,
                    expected_revision=args.expected_revision,
                    display_name=args.display_name,
                    credential_ref=args.credential_ref,
                    ca_ref=args.ca_ref,
                )
            )
        elif action == "retire":
            result = asdict(
                service.retire(args.instance_key, expected_revision=args.expected_revision)
            )
        elif action == "replace-installation":
            result = asdict(
                service.replace_installation(
                    args.instance_key,
                    expected_revision=args.expected_revision,
                    installation_marker=args.installation_marker,
                )
            )
        elif action == "test":
            result = probe_connection(
                service, args.instance_key, expected_revision=args.expected_revision
            )
        elif action == "browse":
            result = browse_source(
                service,
                args.instance_key,
                expected_revision=args.expected_revision,
                kind=args.kind,
                organization=args.organization,
                page=args.page,
            )
        elif action == "approve":
            approve_connection(
                service,
                args.instance_key,
                expected_revision=args.expected_revision,
                evidence_digest=args.evidence_digest,
            )
            result = asdict(service.get(args.instance_key))
        else:
            raise ValueError("Unknown connection command")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, ProfileConflict, ConnectionUnavailable, ProvisioningTargetConflict) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 2
    except (OSError, StateStoreError):
        print(json.dumps({"status": "failed", "error": "Local configuration or state unavailable"}))
        return 2
