#!/usr/bin/env python3
"""LDAPS qualification probe; all identities and data are synthetic."""

from __future__ import annotations

import json
import os
import ssl
from dataclasses import asdict
from pathlib import Path

from gitferry.directory_claims import (
    DirectoryClaimsConfig,
    DirectoryClaimsConflict,
    DirectoryClaimsUnavailable,
    observe_claim,
)
from ldap3 import ALL, MODIFY_REPLACE, Connection, Server, Tls

HOST = os.environ.get("AD_HOST", "synthdc")
CA = os.environ.get("AD_CA", "/run/ad-ca/ca.pem")
BASE = "DC=synth,DC=ad"
ADMIN = "CN=Administrator,CN=Users," + BASE
PASSWORD = "Synthetic-Admin-Only-42!"
USER_PASSWORD = "Synthetic-User-42!"

tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT, ca_certs_file=CA)
server = Server(HOST, port=636, use_ssl=True, tls=tls, get_info=ALL)
conn = Connection(server, user=ADMIN, password=PASSWORD, auto_bind=True)


def add(dn: str, attrs: dict[str, object]) -> None:
    if not conn.add(dn, attributes=attrs):
        raise RuntimeError(conn.result)


try:
    add("OU=People," + BASE, {"objectClass": ["top", "organizationalUnit"]})
    add("OU=Groups," + BASE, {"objectClass": ["top", "organizationalUnit"]})
    add(
        "CN=ldap-same,OU=People," + BASE,
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "sAMAccountName": "ldap-same",
            "userPrincipalName": "ldap-same@synth.ad",
            "displayName": "Synthetic Same Name",
            "userAccountControl": 512,
            "unicodePwd": '"Synthetic-User-42!"'.encode("utf-16-le"),
        },
    )
    add(
        "CN=ad-nomail,OU=People," + BASE,
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "sAMAccountName": "ad-nomail",
            "userPrincipalName": "ad-nomail@synth.ad",
            "userAccountControl": 512,
            "unicodePwd": '"Synthetic-User-43!"'.encode("utf-16-le"),
        },
    )
    add(
        "CN=ad-disabled,OU=People," + BASE,
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "sAMAccountName": "ad-disabled",
            "userPrincipalName": "ad-disabled@synth.ad",
            "userAccountControl": 514,
            "unicodePwd": '"Synthetic-User-44!"'.encode("utf-16-le"),
        },
    )
    add(
        "CN=Direct,OU=Groups," + BASE, {"objectClass": ["top", "group"], "sAMAccountName": "Direct"}
    )
    add(
        "CN=Nested,OU=Groups," + BASE,
        {
            "objectClass": ["top", "group"],
            "sAMAccountName": "Nested",
            "member": "CN=Direct,OU=Groups," + BASE,
        },
    )
    conn.modify(
        "CN=Direct,OU=Groups," + BASE,
        {"member": [(MODIFY_REPLACE, ["CN=ldap-same,OU=People," + BASE])]},
    )
    conn.search(
        BASE,
        "(&(objectClass=user)(sAMAccountName=*))",
        attributes=[
            "distinguishedName",
            "sAMAccountName",
            "mail",
            "objectSid",
            "userAccountControl",
            "memberOf",
        ],
    )
    rows = []
    for entry in conn.entries:
        sid = entry.objectSid.value
        rows.append(
            {
                "dn": entry.entry_dn,
                "username": entry.sAMAccountName.value,
                "mail_present": bool(entry.mail.value),
                "object_sid_hex": sid.hex() if isinstance(sid, bytes) else str(sid),
                "disabled": bool((entry.userAccountControl.value or 0) & 2),
                "member_of": list(entry.memberOf.values or []),
            }
        )
    print(
        json.dumps(
            {
                "status": "passed",
                "server": server.info.vendor_name,
                "server_version": server.info.vendor_version,
                "tls": "LDAPS verified CA",
                "users": rows,
                "nested_group_fixture": "Direct member of Nested",
            },
            sort_keys=True,
        )
    )
    app_claim_config = DirectoryClaimsConfig(
        host=HOST,
        port=636,
        security_protocol="ldaps",
        ca_pem=Path(CA).read_text(),
        search_base="OU=People," + BASE,
        user_filter="(sAMAccountName=%s)",
        login_attribute="sAMAccountName",
        external_id_attribute="objectSid",
        email_attribute="mail",
        group_base="OU=Groups," + BASE,
        bind_dn=ADMIN,
        size_limit=2,
    )
    app_claim = observe_claim(
        app_claim_config,
        "synth-ad",
        "ldap-same",
        PASSWORD,
    )
    if (
        app_claim.external_id_kind != "sid"
        or not app_claim.external_id.startswith("S-1-5-21-")
        or app_claim.email is not None
        or app_claim.groups != ("CN=Direct,OU=Groups," + BASE,)
    ):
        raise RuntimeError("application AD claim differs from fixture facts")
    print(json.dumps({"application_observe_claim": asdict(app_claim)}, sort_keys=True))

    def expect_failure(
        label: str, config: DirectoryClaimsConfig, password: str, login: str = "ldap-same"
    ) -> str:
        try:
            observe_claim(config, "synth-ad", login, password)
        except (DirectoryClaimsConflict, DirectoryClaimsUnavailable):
            return "passed"
        raise RuntimeError(f"negative assertion unexpectedly passed: {label}")

    bad_ca = DirectoryClaimsConfig(
        host=HOST,
        port=636,
        security_protocol="ldaps",
        ca_pem=Path("/run/ad-ca/untrusted.pem").read_text(),
        search_base="OU=People," + BASE,
        user_filter="(sAMAccountName=%s)",
        login_attribute="sAMAccountName",
        external_id_attribute="objectSid",
        bind_dn=ADMIN,
    )
    disabled_filter = DirectoryClaimsConfig(
        host=HOST,
        port=636,
        security_protocol="ldaps",
        ca_pem=Path(CA).read_text(),
        search_base="OU=People," + BASE,
        user_filter="(&(sAMAccountName=%s)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
        login_attribute="sAMAccountName",
        external_id_attribute="objectSid",
        bind_dn=ADMIN,
    )
    negative_results = {
        "wrong_password": expect_failure(
            "wrong_password", app_claim_config, "wrong-synthetic-password"
        ),
        "wrong_ca": expect_failure("wrong_ca", bad_ca, PASSWORD),
        "disabled_filter": expect_failure(
            "disabled_filter", disabled_filter, PASSWORD, "ad-disabled"
        ),
    }
    forms = {}
    for label, bind_user in {
        "upn": "ldap-same@synth.ad",
        "domain_login": r"SYNTH\ldap-same",
    }.items():
        form_conn = Connection(server, user=bind_user, password=USER_PASSWORD, auto_bind=True)
        forms[label] = bool(form_conn.bound)
        form_conn.unbind()
    nested_filter = "(member:1.2.840.113556.1.4.1941:=CN=ldap-same,OU=People," + BASE + ")"
    conn.search("OU=Groups," + BASE, nested_filter, attributes=[], size_limit=8)
    nested_results = [entry.entry_dn for entry in conn.entries]
    if set(nested_results) != {
        "CN=Direct,OU=Groups," + BASE,
        "CN=Nested,OU=Groups," + BASE,
    } or not all(forms.values()):
        raise RuntimeError("AD login forms or nested matching rule did not match fixture")
    print(
        json.dumps(
            {
                "negative_assertions": negative_results,
                "credential_forms": forms,
                "nested_matching_rule_results": nested_results,
            },
            sort_keys=True,
        )
    )
finally:
    conn.unbind()
