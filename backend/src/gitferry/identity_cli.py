"""Host-local identity administration sharing the authenticated application's core."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from gitferry.identity_application import (
    CandidateRequest,
    CheckAccountRequest,
    IdentityAction,
    IdentityApplication,
    RegisterRealm,
)
from gitferry.state_store import StateStore


def configure(parser: argparse.ArgumentParser, database_default: str | None) -> None:
    parser.add_argument(
        "--database", type=Path, default=database_default, required=database_default is None
    )
    actions = parser.add_subparsers(dest="identity_action", required=True)
    actions.add_parser("list")
    for name in ("register-realm", "check-account", "propose", "decide"):
        action = actions.add_parser(name)
        action.add_argument("--input", type=Path, required=True)
        if name == "decide":
            action.add_argument("--candidate-id", required=True)
    events = actions.add_parser("events")
    events.add_argument("--candidate-id", required=True)


def execute(args: argparse.Namespace) -> int:
    # Host write access is the CLI authority; input documents cannot impersonate a web actor.
    try:
        app = IdentityApplication(StateStore(args.database))
        result: Any
        value = {}
        if hasattr(args, "input"):
            with args.input.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("identity request too large")
            value = json.loads(raw)
        if args.identity_action == "list":
            result = app.decisions.list_candidates()
        elif args.identity_action == "events":
            result = app.decisions.events(args.candidate_id)
        elif args.identity_action == "register-realm":
            from dataclasses import asdict

            result = asdict(
                app.state.register_realm(**RegisterRealm.model_validate(value).model_dump())
            )
        elif args.identity_action == "check-account":
            result = app.check_account(CheckAccountRequest.model_validate(value))
        elif args.identity_action == "propose":
            result = app.propose(CandidateRequest.model_validate(value))
        else:
            result = app.decide(
                args.candidate_id, IdentityAction.model_validate(value), actor="local-cli"
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # Never echo remote response or local secret paths in an operator-facing failure.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Identity decision invalid, conflicted or unavailable; "
                    "inspect local candidates.",
                }
            )
        )
        return 2
