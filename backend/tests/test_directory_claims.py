from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from gitferry.directory_claims import (
    DirectoryClaimsConfig,
    DirectoryClaimsConflict,
    DirectoryClaimSnapshot,
    DirectoryClaimsUnavailable,
    normalize_object_sid,
    observe_claim,
)


def config(tmp_path: Path, **changes: object) -> DirectoryClaimsConfig:
    values: dict[str, object] = {
        "host": "ldap.example.invalid",
        "port": 636,
        "security_protocol": "ldaps",
        "ca_pem": "synthetic CA",
        "search_base": "ou=People,dc=example,dc=invalid",
        "user_filter": "(&(objectClass=person)(uid=%s))",
        "login_attribute": "uid",
        "external_id_attribute": "employeeNumber",
        "email_attribute": "mail",
        "group_attribute": "memberOf",
    }
    values.update(changes)
    return DirectoryClaimsConfig(**values)  # type: ignore[arg-type]


def entry(**attributes: object) -> dict[str, object]:
    return {
        "dn": "uid=alice,ou=People,dc=example,dc=invalid",
        "attributes": {
            "uid": ["alice"],
            "employeeNumber": ["EXT-100"],
            "mail": ["alice@example.invalid"],
            "memberOf": ["cn=readers,ou=Groups,dc=example,dc=invalid"],
            **attributes,
        },
    }


def test_transport_receives_escaped_exact_filter_and_bounded_config(tmp_path: Path) -> None:
    calls: list[tuple[str, str | None, str]] = []
    login = "a*)(uid=*" + chr(0) + "lice"

    def transport(config, login_value, password, search_filter):
        calls.append((config.security_protocol, password, search_filter))
        return [entry(uid=[login_value])]

    observed = observe_claim(
        config(
            tmp_path,
            bind_dn="uid=reader,ou=People,dc=example,dc=invalid",
        ),
        "realm-a",
        login,
        "memory",
        transport=transport,
    )
    assert observed.external_id == "EXT-100"
    assert calls == [
        ("ldaps", "memory", "(&(objectClass=person)(uid=a\\2a\\29\\28uid=\\2a\\00lice))")
    ]


def test_missing_optional_mail_is_a_valid_observation(tmp_path: Path) -> None:
    observed = observe_claim(
        config(tmp_path),
        "realm-a",
        "alice",
        transport=lambda *_: [entry(mail=None)],
    )
    assert observed.email is None
    assert observed.groups == ("cn=readers,ou=Groups,dc=example,dc=invalid",)


def test_group_order_is_not_identity_and_receive_timeout_is_linux_compatible(tmp_path):
    from gitferry.directory_claims import DirectoryClaimsError

    first = observe_claim(
        config(tmp_path),
        "realm-a",
        "alice",
        transport=lambda *_: [entry(memberOf=["group-b", "group-a"])],
    )
    repeated = observe_claim(
        config(tmp_path),
        "realm-a",
        "alice",
        expected=first,
        transport=lambda *_: [entry(memberOf=["group-a", "group-b"])],
    )
    assert repeated == first
    with pytest.raises(DirectoryClaimsError):
        config(tmp_path, receive_timeout=10.0).validate()
    with pytest.raises(DirectoryClaimsConflict):
        observe_claim(
            config(tmp_path),
            "realm-a",
            "alice",
            transport=lambda *_: [entry(memberOf=[str(i) for i in range(65)])],
        )


def test_missing_or_ambiguous_entries_fail_closed(tmp_path: Path) -> None:
    responses = ([], [entry(), entry()])
    for response in responses:

        def transport(*_, response=response):
            return response

        with pytest.raises(DirectoryClaimsConflict, match="missing or ambiguous"):
            observe_claim(config(tmp_path), "realm-a", "alice", transport=transport)


def test_multivalued_external_id_and_disabled_filter_result_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(DirectoryClaimsConflict, match="multivalued"):
        observe_claim(
            config(tmp_path),
            "realm-a",
            "alice",
            transport=lambda *_: [entry(employeeNumber=["one", "two"])],
        )
    with pytest.raises(DirectoryClaimsConflict, match="missing or ambiguous"):
        observe_claim(config(tmp_path), "realm-a", "disabled", transport=lambda *_: [])


def test_transport_outage_is_safe_and_does_not_expose_detail(tmp_path: Path) -> None:
    def transport(*_):
        raise RuntimeError("bind password and private host")

    with pytest.raises(DirectoryClaimsUnavailable, match="claim read failed") as error:
        observe_claim(
            config(tmp_path, bind_dn="uid=reader,ou=People,dc=example,dc=invalid"),
            "realm-a",
            "alice",
            "secret",
            transport=transport,
        )
    assert "password" not in str(error.value)
    assert "private host" not in str(error.value)


def test_expected_identity_change_is_conflict_without_rewrite(tmp_path: Path) -> None:
    expected = DirectoryClaimSnapshot(
        directory_realm="realm-a",
        login="alice",
        external_id="OLD",
        dn="uid=alice,ou=People,dc=example,dc=invalid",
        ou_path="ou=People,dc=example,dc=invalid",
        email="alice@example.invalid",
        groups=("cn=readers,ou=Groups,dc=example,dc=invalid",),
    )
    with pytest.raises(DirectoryClaimsConflict, match="changed"):
        observe_claim(
            config(tmp_path),
            "realm-a",
            "alice",
            transport=lambda *_: [entry()],
            expected=expected,
        )


def test_binary_object_sid_normalization_is_strict_and_separate(tmp_path: Path) -> None:
    raw = (
        bytes([1, 2])
        + (5).to_bytes(6, "big")
        + (21).to_bytes(4, "little")
        + (42).to_bytes(4, "little")
    )
    assert normalize_object_sid(raw) == "S-1-5-21-42"
    observed = observe_claim(
        config(tmp_path, external_id_attribute="objectSid"),
        "realm-ad-compatible",
        "alice",
        transport=lambda *_: [entry(objectSid=[raw])],
    )
    assert observed.external_id == "S-1-5-21-42"
    assert observed.external_id_kind == "sid"
    with pytest.raises(DirectoryClaimsConflict):
        normalize_object_sid(b"bad")


def test_starttls_configuration_is_explicit_and_read_only_policy_is_injected(
    tmp_path: Path,
) -> None:
    observed = observe_claim(
        config(tmp_path, security_protocol="starttls"),
        "realm-a",
        "alice",
        transport=lambda cfg, *_: [entry()] if cfg.security_protocol == "starttls" else [],
    )
    assert observed.login == "alice"


def test_bind_dn_and_password_are_an_atomic_pair(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="together"):
        observe_claim(config(tmp_path, bind_dn="uid=reader"), "realm-a", "alice")
    with pytest.raises(ValueError, match="together"):
        observe_claim(config(tmp_path), "realm-a", "alice", "password")


def test_returned_login_must_match_requested_login(tmp_path: Path) -> None:
    with pytest.raises(DirectoryClaimsConflict, match="did not match"):
        observe_claim(
            config(tmp_path),
            "realm-a",
            "alice",
            transport=lambda *_: [entry(uid=["other"])],
        )


def test_default_ldap3_transport_accepts_open_none_and_zero_result_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeConnection:
        closed = False
        result = {"result": 0, "referrals": []}
        response: list[dict[str, object]] = []

        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def open(self):
            return None

        def bind(self):
            return True

        def search(self, *args, **kwargs):
            self.response = []
            return False

        def unbind(self):
            return True

    monkeypatch.setitem(
        sys.modules,
        "ldap3",
        SimpleNamespace(
            ANONYMOUS="ANONYMOUS",
            AUTO_BIND_NONE="NONE",
            SIMPLE="SIMPLE",
            SUBTREE="SUBTREE",
            Connection=FakeConnection,
            Server=lambda *args, **kwargs: (args, kwargs),
            Tls=lambda **kwargs: kwargs,
        ),
    )
    with pytest.raises(DirectoryClaimsConflict, match="missing or ambiguous"):
        observe_claim(config(tmp_path), "realm-a", "alice")


def test_default_ldap3_transport_uses_read_only_bounded_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeConnection:
        closed = False
        result = {"result": 0, "referrals": []}

        def __init__(self, *args, **kwargs):
            captured["kwargs"] = kwargs
            self.response: list[dict[str, object]] = []

        def open(self):
            return None

        def bind(self):
            return True

        def search(self, base, search_filter, **kwargs):
            captured["search"] = (base, search_filter, kwargs)
            self.response = [entry(mail=[])]
            return True

        def unbind(self):
            return True

    monkeypatch.setitem(
        sys.modules,
        "ldap3",
        SimpleNamespace(
            ANONYMOUS="ANONYMOUS",
            AUTO_BIND_NONE="NONE",
            SIMPLE="SIMPLE",
            SUBTREE="SUBTREE",
            Connection=FakeConnection,
            Server=lambda *args, **kwargs: (args, kwargs),
            Tls=lambda **kwargs: kwargs,
        ),
    )
    observed = observe_claim(config(tmp_path), "realm-a", "alice")
    assert observed.email is None
    assert captured["kwargs"]["read_only"] is True
    assert captured["kwargs"]["auto_referrals"] is False
    assert captured["search"][2]["size_limit"] == 2


def test_direct_group_search_uses_escaped_dn_and_bounded_results(tmp_path: Path) -> None:
    captured: list[str] = []
    group_config = config(
        tmp_path,
        group_attribute=None,
        group_base="ou=Groups,dc=example,dc=invalid",
    )

    def transport(_, __, ___, search_filter):
        captured.append(search_filter)
        return {
            "users": [entry()],
            "groups": [{"dn": "cn=readers,ou=Groups,dc=example,dc=invalid"}],
        }

    observed = observe_claim(group_config, "realm-a", "alice", transport=transport)
    assert observed.groups == ("cn=readers,ou=Groups,dc=example,dc=invalid",)
    assert captured == ["(&(objectClass=person)(uid=alice))"]


def test_direct_group_result_over_bound_fails_closed(tmp_path: Path) -> None:
    group_config = config(
        tmp_path,
        group_attribute=None,
        group_base="ou=Groups,dc=example,dc=invalid",
    )
    groups = [{"dn": f"cn=g{index},ou=Groups,dc=example,dc=invalid"} for index in range(65)]
    with pytest.raises(DirectoryClaimsConflict, match="exceeded"):
        observe_claim(
            group_config,
            "realm-a",
            "alice",
            transport=lambda *_: {"users": [entry()], "groups": groups},
        )
