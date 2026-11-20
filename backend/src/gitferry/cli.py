"""Small CLI sharing blueprint application logic with the REST API."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gitferry")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-blueprint")
    validate.add_argument("path", type=Path)

    state = subparsers.add_parser("state")
    state_commands = state.add_subparsers(dest="state_command", required=True)
    database_default = os.environ.get("GITFERRY_STATE_DB")

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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
