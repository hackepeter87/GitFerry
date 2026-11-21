"""Exercise the application LDAP reader inside the isolated directory network."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from gitferry.directory_claims import (
    DirectoryClaimsConfig,
    DirectoryClaimsConflict,
    DirectoryClaimsUnavailable,
    observe_claim,
)


def main() -> None:
    config = DirectoryClaimsConfig(
        host="openldap",
        port=636,
        security_protocol="ldaps",
        ca_pem=Path("/run/directory-ca/ca.pem").read_text(),
        search_base="ou=People,dc=example,dc=invalid",
        user_filter="(&(objectClass=posixAccount)(uid=%s)(!(employeeNumber=DISABLED)))",
        login_attribute="uid",
        external_id_attribute="employeeNumber",
        email_attribute="mail",
        group_base="ou=Groups,dc=example,dc=invalid",
    )
    first = observe_claim(config, "directory-a", "ldap-reader")
    second = observe_claim(
        replace(config, search_base="ou=People-B,dc=example,dc=invalid"),
        "directory-b",
        "ldap-reader",
    )
    if first.external_id == second.external_id or first.ou_path == second.ou_path:
        raise RuntimeError("distinct directory identities were conflated")
    if first.login != second.login or first.directory_realm == second.directory_realm:
        raise RuntimeError("equal-login different-realm fixture was not exercised")
    if first.groups != ("cn=gitea-users,ou=Groups,dc=example,dc=invalid",):
        raise RuntimeError("direct group membership was not observed")
    missing = observe_claim(config, "directory-a", "missing-mail")
    if missing.email is not None:
        raise RuntimeError("missing directory email was not preserved")
    try:
        observe_claim(
            replace(config, search_base="dc=example,dc=invalid"), "directory-a", "ldap-reader"
        )
    except DirectoryClaimsConflict:
        pass
    else:
        raise RuntimeError("duplicate directory login did not conflict")
    try:
        observe_claim(config, "directory-a", "ldap-reader*)(uid=*)")
    except DirectoryClaimsConflict:
        pass
    else:
        raise RuntimeError("escaped login unexpectedly matched")
    bound = replace(config, bind_dn=first.dn)
    if observe_claim(bound, "directory-a", "ldap-reader", "synthetic-directory-pass") != first:
        raise RuntimeError("authenticated directory read changed the identity")
    try:
        observe_claim(bound, "directory-a", "ldap-reader", "wrong-synthetic-password")
    except DirectoryClaimsUnavailable:
        pass
    else:
        raise RuntimeError("wrong directory bind password was accepted")
    print(
        json.dumps(
            {
                "status": "passed",
                "client": "ldap3 2.9.1",
                "transport": "LDAPS with verified CA",
                "first": asdict(first),
                "second": asdict(second),
                "missing_mail": asdict(missing),
                "duplicate_login_conflict": True,
                "filter_escaping": True,
                "real_bind_and_wrong_password": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        chain = []
        cause: BaseException | None = error
        while cause is not None:
            chain.append(
                {
                    "type": type(cause).__name__,
                    "reason": str(cause)
                    if type(cause).__module__.startswith("gitferry.")
                    else None,
                }
            )
            cause = cause.__cause__
        print(json.dumps({"status": "failed", "chain": chain}), file=sys.stderr)
        raise SystemExit(1) from None
