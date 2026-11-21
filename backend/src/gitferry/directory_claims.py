"""Read-only, bounded LDAP directory claim observations.

The transport is injectable so claim normalization and fail-closed policy remain
testable without pretending that a unit test is LDAP qualification evidence.
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

_SAFE_ATTRIBUTE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,127}$")
_SID_TEXT = re.compile(r"^S-([0-9]+)(?:-[0-9]+){1,15}$", re.IGNORECASE)
_MAX_TEXT = 4096


def _validate_attribute(label: str, attribute: str | None, *, required: bool = False) -> None:
    if attribute is None:
        if required:
            raise DirectoryClaimsError(f"LDAP {label} attribute is invalid")
        return
    if not _SAFE_ATTRIBUTE.fullmatch(attribute):
        raise DirectoryClaimsError(f"LDAP {label} attribute is invalid")


class DirectoryClaimsError(ValueError):
    """Invalid local claim-reader configuration or directory response."""


class DirectoryClaimsConflict(DirectoryClaimsError):
    """The directory response is missing, ambiguous, or changed unexpectedly."""


class DirectoryClaimsUnavailable(RuntimeError):
    """The read-only directory transport failed with intentionally safe detail."""


@dataclass(frozen=True)
class DirectoryClaimsConfig:
    host: str
    port: int
    security_protocol: Literal["ldaps", "starttls"]
    ca_pem: str
    search_base: str
    user_filter: str
    login_attribute: str
    external_id_attribute: str
    email_attribute: str | None = None
    group_attribute: str | None = None
    group_base: str | None = None
    group_member_attribute: str = "member"
    group_label: str = "dn"
    bind_dn: str | None = None
    connect_timeout: float = 10.0
    receive_timeout: int = 10
    search_time_limit: int = 10
    size_limit: int = 2
    max_groups: int = 64

    def validate(self) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.host)
            or self.host.startswith(".")
            or self.host.endswith(".")
        ):
            raise DirectoryClaimsError("LDAP host is invalid")
        if not 1 <= self.port <= 65535:
            raise DirectoryClaimsError("LDAP port is invalid")
        if self.security_protocol not in ("ldaps", "starttls"):
            raise DirectoryClaimsError("LDAP security protocol is invalid")
        if (
            not isinstance(self.ca_pem, str)
            or not self.ca_pem
            or len(self.ca_pem.encode("utf-8")) > 1024 * 1024
        ):
            raise DirectoryClaimsError("LDAP CA bundle must be bounded and non-empty")
        if not self.search_base.strip() or len(self.search_base) > _MAX_TEXT:
            raise DirectoryClaimsError("LDAP search base is invalid")
        if (
            len(self.user_filter) > _MAX_TEXT
            or self.user_filter.count("%s") != 1
            or "%" in self.user_filter.replace("%s", "")
        ):
            raise DirectoryClaimsError("LDAP user filter must contain exactly one %s placeholder")
        _validate_attribute("login", self.login_attribute, required=True)
        _validate_attribute("external ID", self.external_id_attribute, required=True)
        _validate_attribute("email", self.email_attribute)
        _validate_attribute("user group", self.group_attribute)
        _validate_attribute("group member", self.group_member_attribute, required=True)
        if self.group_base is not None:
            if not self.group_base.strip() or len(self.group_base) > _MAX_TEXT:
                raise DirectoryClaimsError("LDAP group search base is invalid")
            if self.group_label != "dn" and not _SAFE_ATTRIBUTE.fullmatch(self.group_label):
                raise DirectoryClaimsError("LDAP group label is invalid")
        if self.connect_timeout <= 0 or self.receive_timeout <= 0:
            raise DirectoryClaimsError("LDAP timeouts must be positive")
        if (
            type(self.receive_timeout) is not int
            or self.connect_timeout > 60
            or self.receive_timeout > 60
            or self.search_time_limit < 1
            or self.search_time_limit > 60
            or self.size_limit != 2
            or not 1 <= self.max_groups <= 64
        ):
            raise DirectoryClaimsError("LDAP search bounds are invalid")


@dataclass(frozen=True)
class DirectoryClaimSnapshot:
    directory_realm: str
    login: str
    external_id: str
    dn: str
    ou_path: str
    email: str | None
    groups: tuple[str, ...]
    external_id_kind: Literal["text", "sid"] = "text"


DirectoryTransport = Callable[
    [DirectoryClaimsConfig, str, str | None, str],
    Sequence[Mapping[str, Any]] | Mapping[str, Sequence[Mapping[str, Any]]],
]


def _escape_filter(value: str) -> str:
    escaped = {"\x00": "\\00", "(": "\\28", ")": "\\29", "*": "\\2a", "\\": "\\5c"}
    return "".join(escaped.get(character, character) for character in value)


def normalize_object_sid(value: bytes | bytearray | str) -> str:
    """Normalize a bounded binary or textual SID without asserting AD support."""

    if isinstance(value, str):
        parts = value.split("-")
        if not _SID_TEXT.fullmatch(value) or len(value) > 256 or len(parts) < 4:
            raise DirectoryClaimsConflict("directory object SID is invalid")
        try:
            if int(parts[1]) != 1 or not 0 <= int(parts[2]) <= (1 << 48) - 1:
                raise ValueError
            if any(not 0 <= int(part) <= (1 << 32) - 1 for part in parts[3:]):
                raise ValueError
        except ValueError:
            raise DirectoryClaimsConflict("directory object SID is invalid") from None
        return value.upper().replace("S-", "S-", 1)
    raw = bytes(value)
    if len(raw) < 8 or len(raw) > 68 or raw[0] != 1:
        raise DirectoryClaimsConflict("directory object SID is invalid")
    count = raw[1]
    if count == 0 or count > 15 or len(raw) != 8 + count * 4:
        raise DirectoryClaimsConflict("directory object SID is invalid")
    authority = int.from_bytes(raw[2:8], "big")
    subauthorities = [
        str(int.from_bytes(raw[offset : offset + 4], "little")) for offset in range(8, len(raw), 4)
    ]
    return "S-1-" + str(authority) + "-" + "-".join(subauthorities)


def _one(value: Any, label: str) -> str:
    values = value if isinstance(value, (list, tuple)) else [value]
    if len(values) != 1 or values[0] in (None, "", b""):
        raise DirectoryClaimsConflict(f"directory {label} is missing or multivalued")
    item = values[0]
    if isinstance(item, bytes):
        try:
            item = item.decode("utf-8")
        except UnicodeDecodeError:
            raise DirectoryClaimsConflict(f"directory {label} is not valid text") from None
    if not isinstance(item, str) or not item.strip() or len(item) > _MAX_TEXT:
        raise DirectoryClaimsConflict(f"directory {label} is invalid")
    return item


def _groups(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple)) else [value]
    result: list[str] = []
    if len(values) > 64:
        raise DirectoryClaimsConflict("directory group result exceeded its bound")
    for item in values:
        text = _one(item, "group")
        if text not in result:
            result.append(text)
    return tuple(sorted(result))


def _optional_one(value: Any, label: str) -> str | None:
    if value is None or value == [] or value == ():
        return None
    return _one(value, label)


def _attribute(attributes: Mapping[str, Any], name: str) -> Any:
    for key, value in attributes.items():
        if isinstance(key, str) and key.lower() == name.lower():
            return value
    return None


def _group_entries(entries: Sequence[Mapping[str, Any]], label: str) -> tuple[str, ...]:
    groups: list[str] = []
    for entry in entries:
        attributes = entry.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise DirectoryClaimsConflict("directory group attributes are invalid")
        value = entry.get("dn") if label == "dn" else _attribute(attributes, label)
        group = _one(value, "group")
        if group not in groups:
            groups.append(group)
    return tuple(sorted(groups))


def _ou_path(dn: str) -> str:
    if not dn or len(dn) > _MAX_TEXT or "\x00" in dn:
        raise DirectoryClaimsConflict("directory DN is invalid")
    escaped = False
    for index, character in enumerate(dn):
        if character == "\\":
            escaped = not escaped
        elif character == "," and not escaped:
            path = dn[index + 1 :].strip()
            if not path:
                raise DirectoryClaimsConflict("directory DN has no OU path")
            return path
        else:
            escaped = False
    raise DirectoryClaimsConflict("directory DN has no parent path")


def observe_claim(
    config: DirectoryClaimsConfig,
    directory_realm: str,
    login: str,
    password: str | None = None,
    *,
    transport: DirectoryTransport | None = None,
    expected: DirectoryClaimSnapshot | None = None,
) -> DirectoryClaimSnapshot:
    """Read exactly one directory entry; never approves or rewrites a binding."""

    config.validate()
    if (password is None) != (config.bind_dn is None):
        raise DirectoryClaimsError("LDAP bind DN and password must be supplied together")
    if (
        not directory_realm
        or len(directory_realm) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in directory_realm)
    ):
        raise DirectoryClaimsError("directory realm is invalid")
    if not login or len(login) > _MAX_TEXT:
        raise DirectoryClaimsError("directory login is invalid")
    search_filter = config.user_filter.replace("%s", _escape_filter(login))
    transport_result: Sequence[Mapping[str, Any]] | Mapping[str, Sequence[Mapping[str, Any]]]
    try:
        if transport is None:
            transport_result = _ldap3_transport(config, login, password, search_filter)
        else:
            transport_result = transport(config, login, password, search_filter)
    except DirectoryClaimsError:
        raise
    except Exception as exc:
        raise DirectoryClaimsUnavailable("directory claim read failed") from exc
    if isinstance(transport_result, Mapping):
        entries = transport_result.get("users", ())
        group_entries = transport_result.get("groups", ())
    else:
        entries = transport_result
        group_entries = ()
    if len(entries) != 1:
        raise DirectoryClaimsConflict("directory claim lookup was missing or ambiguous")
    entry = entries[0]
    dn = _one(entry.get("dn"), "DN")
    attributes = entry.get("attributes", entry)
    if not isinstance(attributes, Mapping):
        raise DirectoryClaimsConflict("directory entry attributes are invalid")
    returned_login = _one(_attribute(attributes, config.login_attribute), "login")
    if returned_login != login:
        raise DirectoryClaimsConflict("directory login did not match requested login")
    kind: Literal["text", "sid"] = "text"
    if config.external_id_attribute.lower() == "objectsid":
        raw = _attribute(attributes, config.external_id_attribute)
        if isinstance(raw, (list, tuple)):
            if len(raw) != 1:
                raise DirectoryClaimsConflict("directory object SID is missing or multivalued")
            raw_value = raw[0]
        else:
            raw_value = raw
        if not isinstance(raw_value, (bytes, bytearray, str)):
            raise DirectoryClaimsConflict("directory object SID is invalid")
        external_value = normalize_object_sid(raw_value)
        kind = "sid"
    else:
        external_value = _one(_attribute(attributes, config.external_id_attribute), "external ID")
    email = None
    if config.email_attribute is not None:
        email = _optional_one(_attribute(attributes, config.email_attribute), "email")
    groups: tuple[str, ...] = ()
    if config.group_base is not None:
        if len(group_entries) > 64:
            raise DirectoryClaimsConflict("directory group result exceeded its bound")
        groups = _group_entries(group_entries, config.group_label)
    elif config.group_attribute is not None:
        groups = _groups(_attribute(attributes, config.group_attribute))
    snapshot = DirectoryClaimSnapshot(
        directory_realm=directory_realm,
        login=login,
        external_id=external_value,
        dn=dn,
        ou_path=_ou_path(dn),
        email=email,
        groups=groups,
        external_id_kind=kind,
    )
    if expected is not None and snapshot != expected:
        raise DirectoryClaimsConflict("directory claim changed from expected identity")
    return snapshot


def _ldap3_transport(
    config: DirectoryClaimsConfig,
    login: str,
    password: str | None,
    search_filter: str,
) -> Mapping[str, Sequence[Mapping[str, Any]]]:
    try:
        from ldap3 import ANONYMOUS, AUTO_BIND_NONE, SIMPLE, SUBTREE, Connection, Server, Tls
    except ImportError as exc:
        raise DirectoryClaimsUnavailable("ldap3 client is unavailable") from exc
    tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_data=config.ca_pem)
    server = Server(
        config.host,
        port=config.port,
        use_ssl=config.security_protocol == "ldaps",
        tls=tls,
        connect_timeout=config.connect_timeout,
        get_info=None,
    )
    user = config.bind_dn if password is not None else None
    authentication = SIMPLE if password is not None else ANONYMOUS
    connection = Connection(
        server,
        user=user,
        password=password,
        authentication=authentication,
        auto_bind=AUTO_BIND_NONE,
        auto_referrals=False,
        read_only=True,
        receive_timeout=config.receive_timeout,
        raise_exceptions=False,
    )
    try:
        connection.open()
        if connection.closed:
            raise DirectoryClaimsUnavailable("directory connection failed")
        if config.security_protocol == "starttls" and not connection.start_tls():
            raise DirectoryClaimsUnavailable("directory StartTLS failed")
        if not connection.bind():
            raise DirectoryClaimsUnavailable("directory bind failed")
        attributes = [config.login_attribute, config.external_id_attribute]
        for attribute in (config.email_attribute, config.group_attribute):
            if attribute is not None and attribute not in attributes:
                attributes.append(attribute)
        connection.search(
            config.search_base,
            search_filter,
            search_scope=SUBTREE,
            attributes=attributes,
            size_limit=config.size_limit,
            time_limit=config.search_time_limit,
        )
        _check_ldap_result(connection, "directory search")
        users = _entry_rows(connection.response[: config.size_limit])
        groups: tuple[Mapping[str, Any], ...] = ()
        if config.group_base is not None:
            if len(users) != 1:
                return {"users": users, "groups": ()}
            group_dn = _one(users[0].get("dn"), "DN")
            group_filter = f"({config.group_member_attribute}={_escape_filter(group_dn)})"
            group_attribute = [] if config.group_label == "dn" else [config.group_label]
            connection.search(
                config.group_base,
                group_filter,
                search_scope=SUBTREE,
                attributes=group_attribute,
                size_limit=config.max_groups + 1,
                time_limit=config.search_time_limit,
            )
            _check_ldap_result(connection, "directory group search")
            groups = _entry_rows(connection.response)
            if len(groups) > config.max_groups:
                raise DirectoryClaimsConflict("directory group result exceeded its bound")
        return {"users": users, "groups": groups}
    finally:
        connection.unbind()


def _check_ldap_result(connection: Any, operation: str) -> None:
    result = connection.result if isinstance(connection.result, Mapping) else {}
    code = result.get("result")
    if result.get("referrals") or code == 10:
        raise DirectoryClaimsConflict(f"{operation} returned a referral")
    if code == 4:
        raise DirectoryClaimsConflict(f"{operation} exceeded its result bound")
    if code not in (None, 0):
        raise DirectoryClaimsUnavailable(f"{operation} failed")


def _entry_rows(response: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(response, list):
        raise DirectoryClaimsUnavailable("directory response is invalid")
    rows: list[Mapping[str, Any]] = []
    for row in response:
        if not isinstance(row, Mapping) or row.get("type", "searchResEntry") != "searchResEntry":
            raise DirectoryClaimsConflict("directory response contained a referral or non-entry")
        rows.append(row)
    return tuple(rows)


__all__ = [
    "DirectoryClaimsConfig",
    "DirectoryClaimSnapshot",
    "DirectoryClaimsError",
    "DirectoryClaimsConflict",
    "DirectoryClaimsUnavailable",
    "DirectoryTransport",
    "normalize_object_sid",
    "observe_claim",
]
