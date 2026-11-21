"""Operator CLI for local package admission and draft plan preparation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from gitferry.application_package import PackageError
from gitferry.package_admission import PackageAdmissionConflict, PackageAdmissions
from gitferry.runtime import RuntimePaths
from gitferry.state_store import StateStore, StateStoreError


def configure(parser: argparse.ArgumentParser, database_default: str | None) -> None:
    actions = parser.add_subparsers(dest="intake_action", required=True)

    def common(action: argparse.ArgumentParser) -> None:
        action.add_argument(
            "--database", type=Path, default=database_default, required=database_default is None
        )
        action.add_argument("--package-ref", required=True)
        action.add_argument("--target-instance", required=True)
        action.add_argument("--profile-revision", required=True, type=int)

    listing = actions.add_parser("list")
    listing.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    for name in ("review", "admit", "plan-review", "prepare-plan"):
        action = actions.add_parser(name)
        common(action)
    actions.choices["admit"].add_argument("--expected-digest", required=True)
    actions.choices["prepare-plan"].add_argument("--expected-review-digest", required=True)
    for name in ("plan-review", "prepare-plan"):
        action = actions.choices[name]
        action.add_argument("--exclude-metadata", action="store_true")
        action.add_argument("--apply-members", action="store_true")
        action.add_argument("--author-credential", action="append", default=[])


def _paths(database: Path) -> RuntimePaths:
    return replace(RuntimePaths.from_environment(), state_database=database)


def _refs(values: list[str]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for value in values:
        key, separator, reference = value.partition("=")
        if not separator or not key or not reference or key in refs:
            raise ValueError(
                "author credential bindings must use unique binding-id=reference values"
            )
        refs[key] = reference
    return refs


def execute(args: argparse.Namespace) -> int:
    try:
        state = StateStore(args.database)
        service = PackageAdmissions(state, _paths(args.database))
        action = args.intake_action
        if action == "list":
            result: Any = service.list()
        else:
            common = {
                "target_instance_key": args.target_instance,
                "profile_revision": args.profile_revision,
            }
            if action == "review":
                result = service.review(args.package_ref, **common)
            elif action == "admit":
                result = service.admit(
                    args.package_ref, expected_digest=args.expected_digest, **common
                )
            else:
                options = {
                    "include_metadata": not args.exclude_metadata,
                    "apply_members": args.apply_members,
                    "author_credential_refs": _refs(args.author_credential),
                }
                if action == "plan-review":
                    result = service.plan_review(args.package_ref, **common, **options)
                else:
                    result = service.prepare_plan(
                        args.package_ref,
                        expected_review_digest=args.expected_review_digest,
                        **common,
                        **options,
                    )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        sqlite3.Error,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        PackageError,
        PackageAdmissionConflict,
        StateStoreError,
    ):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Package admission request is invalid or unavailable",
                },
                sort_keys=True,
            )
        )
        return 2


__all__ = ["configure", "execute"]
