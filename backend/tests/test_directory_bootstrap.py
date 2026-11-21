from pathlib import Path

import pytest
from gitferry.directory_bootstrap import (
    DirectoryBootstrapError,
    DirectoryBootstrapUnsupported,
    prepare_ldap_simple_bootstrap,
)

SOURCE = {
    "name": "synthetic-openldap",
    "type": "ldaps",
    "mode": "simple",
    "directoryRealm": "SYNTHETIC-OPENLDAP",
    "host": "ldap.example.invalid",
    "port": 636,
    "userDn": "uid=%s,ou=People,dc=example,dc=invalid",
    "userFilter": "(&(objectClass=posixAccount)(uid=%s))",
    "emailAttribute": "mail",
    "usernameAttribute": "uid",
    "caSecretRef": "trust/internal-directory-ca",
}


def prepare(source: dict) -> object:
    return prepare_ldap_simple_bootstrap(
        source,
        gitea_binary=Path("/usr/local/bin/gitea"),
        gitea_config=Path("/etc/gitea/app.ini"),
        ca_path=Path("/run/gitferry/ca/directory.pem"),
    )


def test_preflight_is_secret_free_and_sets_explicit_ca() -> None:
    result = prepare(SOURCE)
    assert "--bind-password" not in result.command
    assert "synthetic-openldap" in result.command
    assert result.environment == (("SSL_CERT_FILE", "/run/gitferry/ca/directory.pem"),)
    assert len(result.intent_digest) == 64


def test_simple_ldap_requires_starttls() -> None:
    source = {**SOURCE, "type": "ldap"}
    with pytest.raises(DirectoryBootstrapError, match="securityProtocol=starttls"):
        prepare(source)


def test_binddn_is_rejected_with_ui_prerequisite() -> None:
    source = {**SOURCE, "mode": "binddn", "bindSecretRef": "secrets/ldap/bind"}
    with pytest.raises(DirectoryBootstrapUnsupported, match="admin UI"):
        prepare(source)


@pytest.mark.parametrize("field", ["userDn", "userFilter", "emailAttribute"])
def test_required_simple_field_cannot_be_empty(field: str) -> None:
    source = {**SOURCE, field: ""}
    with pytest.raises(DirectoryBootstrapError, match="missing required"):
        prepare(source)


def test_paths_must_be_absolute_and_non_symlink(tmp_path: Path) -> None:
    with pytest.raises(DirectoryBootstrapError, match="absolute"):
        prepare_ldap_simple_bootstrap(
            SOURCE,
            gitea_binary=Path("gitea"),
            gitea_config=tmp_path / "app.ini",
            ca_path=tmp_path / "ca.pem",
        )


def test_ca_reference_is_required() -> None:
    source = {key: value for key, value in SOURCE.items() if key != "caSecretRef"}
    with pytest.raises(DirectoryBootstrapError, match="caSecretRef"):
        prepare(source)
