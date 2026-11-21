"""Synthetic local server used only by the browser authentication contract."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from gitferry import api as api_module
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.state_store import StateStore
from plan_fixture import BrowserPlanAdapter, attach_plan_fixture


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def expire_current_sessions(self) -> None:
        self.value += timedelta(hours=9)


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
    clock = MutableClock()
    auth = AuthService(StateStore(database), clock=clock)
    database.chmod(0o600)
    if args.mode == "bootstrapped" and not auth.bootstrap_completed():
        auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
        for name, value in (
            ("initial-password", "correct synthetic passphrase"),
            ("replacement-password", "replacement synthetic passphrase"),
        ):
            path = runtime_root / name
            path.write_text(value + "\n", encoding="utf-8")
            path.chmod(0o600)
    os.environ.update(
        {
            "GITFERRY_RUNTIME_MANAGED": "1",
            "GITFERRY_STATE_DB": str(database),
            "GITFERRY_PACKAGE_DIR": str(runtime_root.with_name(args.mode + "-packages")),
            "GITFERRY_WORK_DIR": str(runtime_root.with_name(args.mode + "-work")),
        }
    )
    # Only this synthetic test server substitutes a controlled remote adapter.
    # The production worker/journal and HTTP routes remain unchanged.
    api_module.GiteaPlanAdapter = BrowserPlanAdapter
    application = create_app(auth_service=auth, session_cookie_secure=False)
    attach_plan_fixture(application, auth.state)

    @application.post("/__test__/expire-sessions")
    def expire_sessions() -> dict[str, bool]:
        clock.expire_current_sessions()
        return {"expired": True}

    uvicorn.run(
        application,
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
