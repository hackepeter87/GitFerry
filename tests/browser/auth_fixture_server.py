"""Synthetic local server used only by the browser authentication contract."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.state_store import StateStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("bootstrapped", "unbootstrapped"), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    runtime_root_value = os.environ.get("GITFERRY_E2E_ROOT")
    if not runtime_root_value:
        raise RuntimeError("GITFERRY_E2E_ROOT is required")
    runtime_root = Path(runtime_root_value).resolve() / args.mode
    runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = runtime_root / "state.sqlite3"
    auth = AuthService(StateStore(database))
    if args.mode == "bootstrapped" and not auth.bootstrap_completed():
        auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
        for name, value in (
            ("initial-password", "correct synthetic passphrase"),
            ("replacement-password", "replacement synthetic passphrase"),
        ):
            path = runtime_root / name
            path.write_text(value + "\n", encoding="utf-8")
            path.chmod(0o600)
    uvicorn.run(
        create_app(auth_service=auth, session_cookie_secure=False),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
