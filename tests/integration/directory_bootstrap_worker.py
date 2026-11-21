"""Real target-local LDAP-simple bootstrap worker for the isolated fixture."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from gitferry.directory_execution import (
    DirectoryBootstrapConflict,
    DirectoryBootstrapRecord,
    DirectoryBootstrapUnknown,
)
from gitferry.directory_local import DirectoryLocalError, LocalBootstrap
from gitferry.state_store import StateStore

DATABASE = Path("/state/directory-bootstrap.sqlite3")
GITEA_BINARY = Path("/usr/local/bin/gitea")
GITEA_CONFIG = Path("/data/gitea/conf/app.ini")
DIRECTORY_CA = Path("/run/directory-ca/ca.pem")
INSTANCE_KEY = "target"
REALM_KEY = "SYNTHETIC-OPENLDAP"
SOURCE_NAME = "synthetic-openldap-simple"

SOURCE: dict[str, Any] = {
    "name": SOURCE_NAME,
    "type": "ldaps",
    "mode": "simple",
    "directoryRealm": REALM_KEY,
    "host": "openldap",
    "port": 636,
    "userDn": "uid=%s,ou=People,dc=example,dc=invalid",
    "userFilter": "(&(objectClass=posixAccount)(uid=%s)(!(employeeNumber=DISABLED)))",
    "emailAttribute": "mail",
    "usernameAttribute": "uid",
    "caSecretRef": "fixture/directory-ca",
}


def _state() -> StateStore:
    state = StateStore(DATABASE)
    state.register_instance(
        INSTANCE_KEY,
        "Synthetic target Gitea",
        "http://target:3000",
        "target-data",
    )
    state.register_realm(REALM_KEY, "Synthetic OpenLDAP", "realm-marker")
    return state


class _ObservedBootstrap(LocalBootstrap):
    def _run(self, argv: tuple[str, ...], preflight: Any) -> str:
        if "add-ldap-simple" in argv:
            with Path("/state/bootstrap-commands.jsonl").open("a") as stream:
                stream.write(json.dumps({"create": self.source["name"]}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return super()._run(argv, preflight)


class _InterruptedBootstrap(_ObservedBootstrap):
    def __init__(self, *args: Any, interruption: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.interruption = interruption

    def _run(self, argv: tuple[str, ...], preflight: Any) -> str:
        output = super()._run(argv, preflight)
        if "add-ldap-simple" not in argv:
            return output
        name_index = argv.index("--name") + 1
        if name_index >= len(argv) or argv[name_index] != self.source["name"]:
            return output
        if self.interruption == "crash":
            os._exit(73)
        raise DirectoryLocalError("Gitea local command response was lost")


def _bootstrap(
    state: StateStore,
    source: dict[str, Any] | None = None,
    *,
    interruption: str | None = None,
) -> LocalBootstrap:
    source_data = source or dict(SOURCE)
    bootstrap_type = _InterruptedBootstrap if interruption is not None else _ObservedBootstrap
    kwargs: dict[str, Any] = {
        "state": state,
        "instance_key": INSTANCE_KEY,
        "realm_key": REALM_KEY,
        "source": source_data,
        "gitea_binary": GITEA_BINARY,
        "gitea_config": GITEA_CONFIG,
        "ca_path": DIRECTORY_CA,
        "work_path": Path("/data/gitea"),
        "custom_path": Path("/data/gitea"),
    }
    if interruption is not None:
        kwargs["interruption"] = interruption
    return bootstrap_type(**kwargs)


def _source(name: str) -> dict[str, Any]:
    source = dict(SOURCE)
    source["name"] = name
    return source


def _bootstrap_evidence(
    state: StateStore,
    bootstrap: LocalBootstrap,
    source_name: str,
    record: DirectoryBootstrapRecord | None,
) -> dict[str, Any]:
    preflight, _ = bootstrap._preflight()
    listing = bootstrap._list(preflight)
    observed = listing.get(source_name)
    return {
        "create_call_count": sum(
            json.loads(line)["create"] == source_name
            for line in Path("/state/bootstrap-commands.jsonl").read_text().splitlines()
        ),
        "record": _record(record),
        "source_count": len(listing),
        "source_name_count": int(observed is not None),
        "source": asdict(observed) if observed is not None else None,
        "events": bootstrap.journal.events(INSTANCE_KEY, source_name),
    }


def _record(record: DirectoryBootstrapRecord | None) -> dict[str, Any] | None:
    return asdict(record) if record is not None else None


def _evidence(state: StateStore, record: DirectoryBootstrapRecord | None) -> dict[str, Any]:
    return _bootstrap_evidence(state, _bootstrap(state), SOURCE_NAME, record)


def _require_single_source(evidence: dict[str, Any], source_name: str) -> None:
    observed = evidence["source"]
    if evidence["create_call_count"] != 1:
        raise RuntimeError("local bootstrap repeated an external create command")
    if evidence["source_name_count"] != 1 or observed is None or observed["name"] != source_name:
        raise RuntimeError("expected exactly one named LDAP source in CLI readback")


def _unknown_replay(state: StateStore, source_name: str, mode: str) -> dict[str, Any]:
    bootstrap = _bootstrap(state, _source(source_name))
    before = bootstrap.journal.get(INSTANCE_KEY, source_name)
    if before is None or before.status not in {"executing", "outcome_unknown"}:
        raise RuntimeError("unknown replay requires an interrupted bootstrap")
    try:
        bootstrap.apply()
    except DirectoryBootstrapUnknown:
        record = bootstrap.journal.get(INSTANCE_KEY, source_name)
        if (
            record is None
            or record.status != "outcome_unknown"
            or record.native_source_id is not None
        ):
            raise RuntimeError("unknown replay did not preserve an unresolved record") from None
        evidence = _bootstrap_evidence(state, bootstrap, source_name, record)
        _require_single_source(evidence, source_name)
        if evidence["events"][-1]["to_status"] != "outcome_unknown":
            raise RuntimeError("unknown replay did not append the uncertainty transition") from None
        return {"status": "passed", "mode": mode, **evidence}
    raise RuntimeError("unknown replay unexpectedly executed a second create")


def run(mode: str) -> dict[str, Any]:
    state = _state()
    bootstrap = _bootstrap(state)
    if mode == "apply":
        record = bootstrap.apply()
        if record.status != "confirmed" or record.native_source_id is None:
            raise RuntimeError("real LDAP-simple bootstrap did not confirm")
        evidence = _evidence(state, record)
        _require_single_source(evidence, SOURCE_NAME)
        return {"status": "passed", "mode": mode, **evidence}
    if mode == "replay":
        before = bootstrap.journal.get(INSTANCE_KEY, SOURCE_NAME)
        if before is None or before.status != "confirmed":
            raise RuntimeError("replay requires a confirmed bootstrap")
        before_events = bootstrap.journal.events(INSTANCE_KEY, SOURCE_NAME)
        record = bootstrap.apply()
        if record.native_source_id != before.native_source_id:
            raise RuntimeError("replay changed the native source ID")
        evidence = _evidence(state, record)
        _require_single_source(evidence, SOURCE_NAME)
        if evidence["events"] != before_events:
            raise RuntimeError("confirmed replay appended an unexpected event")
        return {"status": "passed", "mode": mode, **evidence}
    if mode == "changed-intent":
        changed = dict(SOURCE)
        changed["userFilter"] = "(&(objectClass=posixAccount)(uid=%s))"
        try:
            _bootstrap(state, changed).apply()
        except DirectoryBootstrapConflict:
            changed_record = bootstrap.status()
            if changed_record is None or changed_record.status != "confirmed":
                raise RuntimeError("changed intent did not preserve confirmed record") from None
            return {"status": "passed", "mode": mode, **_evidence(state, changed_record)}
        raise RuntimeError("changed intent unexpectedly succeeded")
    if mode == "crash":
        crash_name = f"{SOURCE_NAME}-crash"
        _bootstrap(state, _source(crash_name), interruption="crash").apply()
        raise RuntimeError("crash interruption unexpectedly returned")
    if mode == "lost-response":
        lost_name = f"{SOURCE_NAME}-lost"
        bootstrap = _bootstrap(state, _source(lost_name), interruption="lost-response")
        try:
            bootstrap.apply()
        except DirectoryBootstrapUnknown:
            lost_record = bootstrap.journal.get(INSTANCE_KEY, lost_name)
            if (
                lost_record is None
                or lost_record.status != "outcome_unknown"
                or lost_record.native_source_id is not None
            ):
                raise RuntimeError("lost response did not persist an unresolved record") from None
            evidence = _bootstrap_evidence(state, bootstrap, lost_name, lost_record)
            _require_single_source(evidence, lost_name)
            return {"status": "passed", "mode": mode, **evidence}
        raise RuntimeError("lost response unexpectedly confirmed")
    if mode == "unknown-replay-crash":
        return _unknown_replay(state, f"{SOURCE_NAME}-crash", mode)
    if mode == "unknown-replay-lost-response":
        return _unknown_replay(state, f"{SOURCE_NAME}-lost", mode)
    if mode == "status":
        status_record = bootstrap.status()
        return {"status": "passed", "mode": mode, **_evidence(state, status_record)}
    raise ValueError(f"unsupported mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "apply",
            "replay",
            "changed-intent",
            "status",
            "crash",
            "lost-response",
            "unknown-replay-crash",
            "unknown-replay-lost-response",
        ),
    )
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.mode), sort_keys=True))
    except Exception as error:
        chain = []
        cause: BaseException | None = error
        while cause is not None:
            chain.append(
                {
                    "type": type(cause).__name__,
                    "reason": str(cause) if isinstance(cause, DirectoryLocalError) else None,
                    "frames": [
                        {"function": frame.name, "line": frame.lineno}
                        for frame in traceback.extract_tb(cause.__traceback__)
                    ],
                }
            )
            cause = cause.__cause__
        print(json.dumps({"mode": args.mode, "failure": chain}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
