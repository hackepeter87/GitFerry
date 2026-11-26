"""Small CLI sharing blueprint application logic with the REST API."""

from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from gitferry import (
    directory_cli,
    identity_cli,
    inventory_cli,
    package_admission_cli,
    package_cli,
    plan_cli,
    profile_cli,
    source_export_cli,
)
from gitferry.auth import AuthError, AuthService
from gitferry.blueprints import BlueprintValidationError, load_and_validate
from gitferry.comment_operations import resolve_unknown_comment_operation
from gitferry.gitea_comments import CommentTargetConflict, GiteaCommentClient
from gitferry.state_store import StateStore, StateStoreError
from gitferry.transport import (
    ManifestValidationError,
    load_transport,
    read_document,
    write_transport,
)
from gitferry.transport_state import admit_transport, record_export

MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_SECRET_FILE_BYTES = 4096


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gitferry")
    subparsers = parser.add_subparsers(dest="command", required=True)
    database_default = os.environ.get("GITFERRY_STATE_DB")
    package_cli.configure(subparsers.add_parser("packages"))
    profile_cli.configure(subparsers.add_parser("connections"), database_default)
    plan_cli.configure(subparsers.add_parser("plans"), database_default)
    source_export_cli.configure(subparsers.add_parser("exports"), database_default)
    inventory_cli.configure(subparsers.add_parser("inventories"), database_default)
    package_admission_cli.configure(subparsers.add_parser("intake"), database_default)
    identity_cli.configure(subparsers.add_parser("identities"), database_default)
    directory_cli.configure(subparsers.add_parser("directories"), database_default)
    validate = subparsers.add_parser("validate-blueprint")
    validate.add_argument("path", type=Path)

    admin = subparsers.add_parser("admin")
    admin_commands = admin.add_subparsers(dest="admin_command", required=True)
    bootstrap = admin_commands.add_parser("bootstrap")
    bootstrap.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--password-file", type=Path, required=True)
    recover = admin_commands.add_parser("recover")
    recover.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    recover.add_argument("--password-file", type=Path, required=True)
    admin_status = admin_commands.add_parser("status")
    admin_status.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )

    state = subparsers.add_parser("state")
    state_commands = state.add_subparsers(dest="state_command", required=True)

    operation = state_commands.add_parser("operation")
    operation.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    operation.add_argument("--operation-id", required=True)

    resolve = state_commands.add_parser("resolve-unknown")
    resolve.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    resolve.add_argument("--operation-id", required=True)
    resolve.add_argument("--target-global-id", required=True)
    resolve.add_argument("--target-issue-index", type=int)
    resolve.add_argument("--observed-intent", type=Path, required=True)
    resolve.add_argument("--evidence", required=True)

    resolve_comment = state_commands.add_parser("resolve-unknown-comment")
    resolve_comment.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    resolve_comment.add_argument("--operation-id", required=True)
    resolve_comment.add_argument("--target-comment-id", required=True)
    resolve_comment.add_argument("--username", required=True)
    resolve_comment.add_argument("--password-env", default="GITFERRY_GITEA_PASSWORD")
    resolve_comment.add_argument("--ca-file", type=Path)

    transport = subparsers.add_parser("transport")
    transport_commands = transport.add_subparsers(dest="transport_command", required=True)
    create = transport_commands.add_parser("create")
    create.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    create.add_argument("--local-instance", required=True)
    create.add_argument("--input", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = transport_commands.add_parser("verify")
    verify.add_argument("path", type=Path)
    admit = transport_commands.add_parser("admit")
    admit.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    admit.add_argument("--local-instance", required=True)
    admit.add_argument("path", type=Path)
    return parser


def _load_evidence(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("observed intent evidence is too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("observed intent evidence must be a JSON object")
    return value


def _load_password_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("password file cannot be opened safely") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("password file must be a regular file")
        if stat.S_IMODE(details.st_mode) & 0o022:
            raise ValueError("password file must not be writable by group or other users")
        if details.st_size > MAX_SECRET_FILE_BYTES:
            raise ValueError("password file is too large")
        content = bytearray()
        while len(content) <= MAX_SECRET_FILE_BYTES:
            chunk = os.read(descriptor, min(1024, MAX_SECRET_FILE_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > MAX_SECRET_FILE_BYTES:
            raise ValueError("password file is too large")
    finally:
        os.close(descriptor)
    try:
        password = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("password file must contain UTF-8 text") from exc
    if password.endswith("\n"):
        password = password[:-1]
        if password.endswith("\r"):
            password = password[:-1]
    if "\n" in password or "\r" in password or "\x00" in password:
        raise ValueError("password file must contain exactly one line")
    return password


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "packages":
        return package_cli.execute(args)
    if args.command == "connections":
        return profile_cli.execute(args)
    if args.command == "plans":
        return plan_cli.execute(args)
    if args.command == "exports":
        return source_export_cli.execute(args)
    if args.command == "inventories":
        return inventory_cli.execute(args)
    if args.command == "directories":
        return directory_cli.execute(args)
    if args.command == "identities":
        return identity_cli.execute(args)
    if args.command == "intake":
        return package_admission_cli.execute(args)
    if args.command == "admin":
        try:
            auth = AuthService(StateStore(args.database))
            if args.admin_command == "status":
                print(json.dumps({"bootstrapRequired": not auth.bootstrap_completed()}))
                return 0
            admin_password = _load_password_file(args.password_file)
            if args.admin_command == "bootstrap":
                account = auth.bootstrap_admin(args.username, admin_password)
                print(
                    json.dumps(
                        {
                            "status": "bootstrapped",
                            "username": account.username,
                            "role": account.role,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            account = auth.recover_bootstrap_admin(admin_password)
            print(
                json.dumps(
                    {
                        "status": "recovered",
                        "username": account.username,
                        "sessions": "revoked",
                    },
                    sort_keys=True,
                )
            )
            return 0
        except (OSError, ValueError, AuthError, StateStoreError) as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
            return 2
    if args.command == "validate-blueprint":
        try:
            document = load_and_validate(args.path)
        except (OSError, BlueprintValidationError) as exc:
            print(json.dumps({"valid": False, "error": str(exc)}))
            return 2
        print(json.dumps({"valid": True, "kind": document["kind"]}))
        return 0
    if args.command == "state":
        try:
            state = StateStore(args.database)
            if args.state_command == "operation":
                operation = state.get_operation(args.operation_id)
                evidence = state.operation_evidence(args.operation_id)
                print(
                    json.dumps(
                        {
                            "operation_id": operation.operation_id,
                            "status": operation.status,
                            "origin_id": operation.origin_id,
                            "target_instance": operation.target_instance_key,
                            "target_repository": operation.target_repository_scope,
                            "operation_type": operation.operation_type,
                            "mapping_version": operation.mapping_version,
                            "baseline_id": operation.baseline_id,
                            "intent_hash": operation.intent_hash,
                            "target": {
                                "global_id": operation.target_global_id,
                                "issue_index": operation.target_issue_index,
                            },
                            "reason": operation.error_reason,
                            **evidence,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            if args.state_command == "resolve-unknown":
                from gitferry.native_operations import NATIVE_OPERATION_TYPES

                if state.get_operation(args.operation_id).operation_type in NATIVE_OPERATION_TYPES:
                    raise ValueError(
                        "native payloads require plans resolve-native with live target verification"
                    )
                operation = state.resolve_unknown_operation(
                    args.operation_id,
                    target_global_id=args.target_global_id,
                    target_issue_index=args.target_issue_index,
                    observed_intent=_load_evidence(args.observed_intent),
                    evidence=args.evidence,
                )
                print(
                    json.dumps(
                        {
                            "operation_id": operation.operation_id,
                            "status": operation.status,
                            "target": {
                                "global_id": operation.target_global_id,
                                "issue_index": operation.target_issue_index,
                            },
                        },
                        sort_keys=True,
                    )
                )
                return 0
            if args.state_command == "resolve-unknown-comment":
                password = os.environ.get(args.password_env)
                if password is None:
                    raise ValueError(
                        f"comment reconciliation password environment is unset: {args.password_env}"
                    )
                operation = state.get_operation(args.operation_id)
                instance = state.get_instance(operation.target_instance_key)
                verify: bool | str = str(args.ca_file) if args.ca_file else True
                with GiteaCommentClient(
                    instance.base_url,
                    username=args.username,
                    password=password,
                    verify=verify,
                ) as comments:
                    resolved = resolve_unknown_comment_operation(
                        state,
                        comments,
                        args.operation_id,
                        target_comment_id=args.target_comment_id,
                    )
                print(
                    json.dumps(
                        {
                            "operation_id": resolved.operation_id,
                            "status": resolved.status,
                            "target": {
                                "global_id": resolved.target_global_id,
                                "issue_index": resolved.target_issue_index,
                            },
                        },
                        sort_keys=True,
                    )
                )
                return 0
        except (
            OSError,
            ValueError,
            KeyError,
            StateStoreError,
            CommentTargetConflict,
            httpx.HTTPError,
        ) as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
            return 2
    if args.command == "transport":
        try:
            if args.transport_command == "verify":
                artifact = load_transport(args.path)
                print(
                    json.dumps(
                        {
                            "valid": True,
                            "manifest_id": artifact.document["manifest_id"],
                            "digest_sha256": artifact.digest_sha256,
                            "mode": artifact.document["mode"],
                            "objects": len(artifact.document["objects"]),
                        },
                        sort_keys=True,
                    )
                )
                return 0
            state = StateStore(args.database)
            if args.transport_command == "create":
                artifact = write_transport(args.output, read_document(args.input))
                report = record_export(state, artifact, local_instance_key=args.local_instance)
            else:
                artifact = load_transport(args.path)
                report = admit_transport(state, artifact, local_instance_key=args.local_instance)
            print(
                json.dumps(
                    {
                        "manifest_id": report.manifest_id,
                        "digest_sha256": report.digest_sha256,
                        "status": report.status,
                        "origins": list(report.origin_ids),
                        "blocked_operations": list(report.blocked_operations),
                    },
                    sort_keys=True,
                )
            )
            return 0
        except (OSError, ValueError, KeyError, StateStoreError, ManifestValidationError) as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
            return 2
    return 2
