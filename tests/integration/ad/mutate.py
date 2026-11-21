#!/usr/bin/env python3
"""Mutate one synthetic Samba AD user and prove each readback over LDAPS."""

from __future__ import annotations

import argparse
import json
import ssl

from gitferry.directory_claims import normalize_object_sid
from ldap3 import MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE, Connection, Server, Tls

BASE = "DC=synth,DC=ad"
USER_DN = "CN=ldap-same,OU=People," + BASE
DIRECT_DN = "CN=Direct,OU=Groups," + BASE
NESTED_DN = "CN=Nested,OU=Groups," + BASE
ADMIN_DN = "CN=Administrator,CN=Users," + BASE
PASSWORD = "Synthetic-Admin-Only-42!"
USER_PASSWORD = "Synthetic-User-42!"


def connection() -> Connection:
    tls = Tls(
        validate=ssl.CERT_REQUIRED,
        version=ssl.PROTOCOL_TLS_CLIENT,
        ca_certs_file="/run/ad-ca/ca.pem",
    )
    server = Server("synthdc", port=636, use_ssl=True, tls=tls, connect_timeout=5)
    return Connection(
        server,
        user=ADMIN_DN,
        password=PASSWORD,
        auto_bind=True,
        auto_referrals=False,
        receive_timeout=10,
    )


def read_user(conn: Connection) -> dict[str, object]:
    if not conn.search(
        USER_DN,
        "(objectClass=user)",
        attributes=[
            "distinguishedName",
            "sAMAccountName",
            "objectSid",
            "userAccountControl",
            "memberOf",
        ],
    ):
        raise RuntimeError("synthetic AD user read failed")
    if len(conn.entries) != 1:
        raise RuntimeError("synthetic AD user read was not singular")
    entry = conn.entries[0]
    sid = entry.objectSid.value
    groups = list(entry.memberOf.values or [])
    return {
        "dn": entry.entry_dn,
        "login": entry.sAMAccountName.value,
        "sid": normalize_object_sid(sid),
        "uac": int(entry.userAccountControl.value or 0),
        "direct_groups": sorted(groups),
    }


def assert_group(conn: Connection, present: bool) -> None:
    ok = conn.modify(NESTED_DN, {"member": [(MODIFY_ADD if present else MODIFY_DELETE, [USER_DN])]})
    if not ok:
        raise RuntimeError("synthetic AD group add failed")
    state = read_user(conn)
    in_groups = NESTED_DN in state["direct_groups"]
    if in_groups is not present:
        raise RuntimeError("synthetic AD group mutation readback failed")


def assert_enabled(conn: Connection, enabled: bool) -> None:
    expected = 512 if enabled else 514
    if not conn.modify(USER_DN, {"userAccountControl": [(MODIFY_REPLACE, [expected])]}):
        raise RuntimeError("synthetic AD UAC mutation failed")
    state = read_user(conn)
    if state["uac"] != expected:
        raise RuntimeError("synthetic AD UAC mutation readback failed")


def recreate(conn: Connection) -> tuple[dict[str, object], dict[str, object]]:
    before = read_user(conn)
    if not conn.delete(USER_DN) or read_user_safe(conn) is not None:
        raise RuntimeError("synthetic AD user delete failed")
    attrs = {
        "objectClass": ["top", "person", "organizationalPerson", "user"],
        "sAMAccountName": "ldap-same",
        "userPrincipalName": "ldap-same@synth.ad",
        "displayName": "Synthetic Same Name",
    }
    if not conn.add(USER_DN, attributes=attrs):
        raise RuntimeError("synthetic AD user recreate failed")
    if not conn.modify(
        USER_DN,
        {
            "unicodePwd": [(MODIFY_REPLACE, [('"' + USER_PASSWORD + '"').encode("utf-16-le")])],
            "userAccountControl": [(MODIFY_REPLACE, [512])],
        },
    ):
        raise RuntimeError("synthetic AD recreated password/state failed")
    if not conn.modify(DIRECT_DN, {"member": [(MODIFY_ADD, [USER_DN])]}):
        raise RuntimeError("synthetic AD direct group restore failed")
    after = read_user(conn)
    if (
        after["dn"] != before["dn"]
        or after["login"] != before["login"]
        or after["sid"] == before["sid"]
    ):
        raise RuntimeError("synthetic AD recreate did not prove same-login new SID")
    return before, after


def read_user_safe(conn: Connection) -> dict[str, object] | None:
    try:
        return read_user(conn)
    except RuntimeError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("group-add", "group-remove", "disable", "enable", "recreate")
    )
    args = parser.parse_args()
    conn = connection()
    try:
        before = read_user(conn)
        if args.mode == "group-add":
            assert_group(conn, True)
        elif args.mode == "group-remove":
            assert_group(conn, False)
        elif args.mode == "disable":
            assert_enabled(conn, False)
        elif args.mode == "enable":
            assert_enabled(conn, True)
        else:
            before, after = recreate(conn)
            print(json.dumps({"mode": args.mode, "before": before, "after": after}, sort_keys=True))
            return
        after = read_user(conn)
        print(json.dumps({"mode": args.mode, "before": before, "after": after}, sort_keys=True))
    finally:
        conn.unbind()


if __name__ == "__main__":
    main()
