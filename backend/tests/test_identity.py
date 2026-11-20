import pytest
from gitferry.identity import (
    ExistingBinding,
    ExplicitIdentityMapping,
    ExternalIdentity,
    IdentityKey,
    IdentityResolutionError,
    LocalAccount,
    create_historical_author_binding,
    create_permission_assignment,
    resolve_identity,
)


def identity(realm: str, external_id: str, ou_path: str) -> ExternalIdentity:
    return ExternalIdentity(
        key=IdentityKey(
            realm=realm,
            login="sam",
            external_id=external_id,
            ou_path=ou_path,
        ),
        source_numeric_id=17,
        email=None,
    )


def account(login: str = "sam-target", *, is_admin: bool = False) -> LocalAccount:
    return LocalAccount(
        realm="TARGET-GITEA",
        login=login,
        numeric_id=41,
        is_admin=is_admin,
    )


def mapping(source: ExternalIdentity, target_login: str = "sam-target") -> ExplicitIdentityMapping:
    return ExplicitIdentityMapping(
        source=source.key,
        target_realm="TARGET-GITEA",
        target_login=target_login,
    )


def test_explicit_mapping_resolves_without_email_and_keeps_concepts_separate() -> None:
    source = identity("REALM-A", "S-1-5-21-A-1001", "OU=People,DC=a,DC=invalid")

    resolution = resolve_identity(source, [mapping(source)], [account()], [])

    assert resolution.status == "resolved"
    assert resolution.external_identity == source
    assert resolution.local_account == account()
    assert resolution.historical_author is None
    assert resolution.permission_assignment is None

    author = create_historical_author_binding(resolution)
    permission = create_permission_assignment(resolution, "ferry-lab/selected-repo", "read")
    assert author.native_account_id == 41
    assert author.origin == source.key
    assert permission.local_account_id == 41
    assert permission.repository == "ferry-lab/selected-repo"
    assert permission.role == "read"


def test_same_login_in_another_realm_does_not_match_without_explicit_rule() -> None:
    realm_a = identity("REALM-A", "S-1-5-21-A-1001", "OU=People,DC=a,DC=invalid")
    realm_b = identity("REALM-B", "S-1-5-21-B-9001", "OU=People,DC=b,DC=invalid")

    resolution = resolve_identity(realm_b, [mapping(realm_a)], [account()], [])

    assert resolution.status == "unmapped"
    assert resolution.local_account is None


def test_multiple_explicit_rules_are_reported_as_ambiguous() -> None:
    source = identity("REALM-A", "S-1-5-21-A-1001", "OU=People,DC=a,DC=invalid")

    resolution = resolve_identity(
        source,
        [mapping(source, "sam-target"), mapping(source, "sam-other")],
        [account(), account("sam-other")],
        [],
    )

    assert resolution.status == "ambiguous"
    assert "multiple explicit mappings" in resolution.conflicts[0]


def test_existing_binding_to_another_external_identity_is_a_collision() -> None:
    source = identity("REALM-A", "S-1-5-21-A-1001", "OU=People,DC=a,DC=invalid")
    other = identity("REALM-B", "S-1-5-21-B-9001", "OU=People,DC=b,DC=invalid")

    resolution = resolve_identity(
        source,
        [mapping(source)],
        [account()],
        [ExistingBinding(local_account_id=41, external_identity=other.key)],
    )

    assert resolution.status == "collision"
    assert "already bound" in resolution.conflicts[0]


def test_mapping_to_administrator_is_rejected_as_privilege_expansion() -> None:
    source = identity("REALM-A", "S-1-5-21-A-1001", "OU=People,DC=a,DC=invalid")

    resolution = resolve_identity(source, [mapping(source)], [account(is_admin=True)], [])

    assert resolution.status == "collision"
    assert "administrator" in resolution.conflicts[0]


def test_unresolved_identity_cannot_be_used_for_author_or_permission() -> None:
    source = identity("REALM-A", "S-1-5-21-A-1001", "OU=People,DC=a,DC=invalid")
    resolution = resolve_identity(source, [], [account()], [])

    with pytest.raises(IdentityResolutionError, match="unmapped"):
        create_historical_author_binding(resolution)
    with pytest.raises(IdentityResolutionError, match="unmapped"):
        create_permission_assignment(resolution, "ferry-lab/selected-repo", "read")


def test_permission_role_is_allowlisted() -> None:
    source = identity("REALM-A", "S-1-5-21-A-1001", "OU=People,DC=a,DC=invalid")
    resolution = resolve_identity(source, [mapping(source)], [account()], [])

    with pytest.raises(ValueError, match="unsupported permission role"):
        create_permission_assignment(resolution, "ferry-lab/selected-repo", "owner")
