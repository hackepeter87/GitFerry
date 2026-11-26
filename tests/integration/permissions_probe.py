"""Organization, identity mapping, and effective Git permission probe."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gitea_compat import PRIMARY_GITEA_VERSION
from gitferry.identity import (
    ExplicitIdentityMapping,
    ExternalIdentity,
    IdentityKey,
    LocalAccount,
    create_permission_assignment,
    resolve_identity,
)
from support import (
    ADMIN,
    SOURCE_URL,
    TARGET_URL,
    USER_PASSWORD,
    WORK,
    ProbeFailure,
    api,
    authenticated_url,
    create_user,
    request,
    run,
)

ORG = "access-lab"
SELECTED_REPO = f"{ORG}/selected-private"
CONTROL_REPO = f"{ORG}/control-private"
USERS = (
    "perm-reader",
    "perm-writer",
    "perm-outsider",
    "perm-late",
    "issue-author",
    "comment-author",
    "comment-author-b",
    "gitferry-importer",
)


@dataclass(frozen=True)
class AccessContext:
    source_users: dict[str, dict[str, Any]]
    target_users: dict[str, dict[str, Any]]
    source_teams: dict[str, dict[str, Any]]
    target_teams: dict[str, dict[str, Any]]
    mappings: dict[str, dict[str, Any]]


def create_team(base_url: str, name: str, code_permission: str) -> dict[str, Any]:
    return api(
        base_url,
        "POST",
        f"/orgs/{ORG}/teams",
        json_body={
            "name": name,
            "permission": code_permission,
            "can_create_org_repo": False,
            "includes_all_repositories": False,
            "units_map": {
                "repo.code": code_permission,
                "repo.issues": code_permission,
            },
        },
    )


def create_structure(base_url: str) -> dict[str, dict[str, Any]]:
    api(
        base_url,
        "POST",
        "/orgs",
        json_body={"username": ORG, "visibility": "private"},
    )
    teams = {
        "readers": create_team(base_url, "readers", "read"),
        "writers": create_team(base_url, "writers", "write"),
    }
    for name in ("selected-private", "control-private"):
        api(
            base_url,
            "POST",
            f"/orgs/{ORG}/repos",
            json_body={"name": name, "private": True, "auto_init": name == "control-private"},
        )
    for team in teams.values():
        api(base_url, "PUT", f"/teams/{team['id']}/repos/{SELECTED_REPO}")
    return teams


def seed_selected_repositories() -> Path:
    repository = WORK / "permission-seed"
    shutil.rmtree(repository, ignore_errors=True)
    repository.mkdir(parents=True)
    run(["git", "init", "-b", "main"], cwd=repository)
    run(["git", "config", "user.name", "Synthetic Permission Probe"], cwd=repository)
    run(["git", "config", "user.email", "permissions@example.invalid"], cwd=repository)
    (repository / "README.md").write_text("# Permission probe\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repository)
    run(["git", "commit", "-m", "Seed permission probe"], cwd=repository)
    for name, base_url in (("source", SOURCE_URL), ("target", TARGET_URL)):
        url = authenticated_url(base_url, f"/{SELECTED_REPO}.git", ADMIN)
        run(["git", "remote", "add", name, url], cwd=repository)
        run(["git", "push", name, "main"], cwd=repository)
    return repository


def git_refs(base_url: str, repository: str, auth: tuple[str, str] = ADMIN) -> dict[str, str]:
    output = run(
        ["git", "ls-remote", authenticated_url(base_url, f"/{repository}.git", auth)]
    ).stdout
    return {
        ref: object_id
        for object_id, ref in (line.split("\t", 1) for line in output.splitlines())
        if ref != "HEAD"
    }


def target_account_names() -> tuple[str, ...]:
    users = api(TARGET_URL, "GET", "/admin/users?page=1&limit=50")
    return tuple(sorted(user["login"] for user in users))


def identity_for(username: str) -> ExternalIdentity:
    realm = "SOURCE-DIRECTORY-A" if username != "perm-outsider" else "SOURCE-DIRECTORY-B"
    suffix = USERS.index(username) + 1001
    return ExternalIdentity(
        key=IdentityKey(
            realm=realm,
            login=username,
            external_id=f"S-1-5-21-{realm[-1]}-{suffix}",
            ou_path=f"OU=People,DC={realm[-1].lower()},DC=example,DC=invalid",
        ),
        email=None,
    )


def resolve_mapping(
    username: str,
    target_users: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], LocalAccount]:
    external = identity_for(username)
    target = target_users[username]
    local = LocalAccount(
        realm="TARGET-GITEA",
        login=username,
        numeric_id=target["id"],
        is_admin=target["is_admin"],
    )
    resolution = resolve_identity(
        external,
        [
            ExplicitIdentityMapping(
                source=external.key,
                target_realm="TARGET-GITEA",
                target_login=username,
            )
        ],
        [local],
        [],
    )
    if resolution.status != "resolved":
        raise ProbeFailure(f"explicit mapping for {username} did not resolve: {resolution}")
    return (
        {
            "source": {
                "realm": external.key.realm,
                "login": external.key.login,
                "external_id": external.key.external_id,
                "ou_path": external.key.ou_path,
                "email": external.email,
            },
            "target": {
                "realm": local.realm,
                "login": local.login,
                "gitea_id": local.numeric_id,
            },
            "resolution": resolution.status,
        },
        local,
    )


def add_mapped_member(
    username: str,
    role: str,
    team: dict[str, Any],
    target_users: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mapping, _ = resolve_mapping(username, target_users)
    external = identity_for(username)
    resolution = resolve_identity(
        external,
        [ExplicitIdentityMapping(external.key, "TARGET-GITEA", username)],
        [LocalAccount("TARGET-GITEA", username, target_users[username]["id"], False)],
        [],
    )
    assignment = create_permission_assignment(resolution, SELECTED_REPO, role)
    api(TARGET_URL, "PUT", f"/teams/{team['id']}/members/{username}")
    mapping["permission_assignment"] = {
        "repository": assignment.repository,
        "role": assignment.role,
        "local_account_id": assignment.local_account_id,
    }
    return mapping


def assert_team_configuration(
    source_teams: dict[str, dict[str, Any]], target_teams: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for name in ("readers", "writers"):
        source = api(SOURCE_URL, "GET", f"/teams/{source_teams[name]['id']}")
        target = api(TARGET_URL, "GET", f"/teams/{target_teams[name]['id']}")
        fields = {
            "permission": source["permission"] == target["permission"],
            "repo.code": source["units_map"].get("repo.code")
            == target["units_map"].get("repo.code"),
            "repo.issues": source["units_map"].get("repo.issues")
            == target["units_map"].get("repo.issues"),
            "selected_repository_assigned": request(
                TARGET_URL,
                "GET",
                f"/teams/{target['id']}/repos/{SELECTED_REPO}",
            ).status_code
            == 200,
            "control_repository_not_assigned": request(
                TARGET_URL,
                "GET",
                f"/teams/{target['id']}/repos/{CONTROL_REPO}",
            ).status_code
            == 404,
        }
        if not all(fields.values()):
            raise ProbeFailure(f"team configuration differs for {name}: {fields}")
        comparison[name] = fields
    return comparison


def run_permissions_probe() -> tuple[dict[str, Any], AccessContext]:
    source_users = {username: create_user(SOURCE_URL, username) for username in USERS}
    # Keep every functional target ID outside the source range even when the
    # synthetic identity set grows; numeric equality must never masquerade as mapping.
    for index in range(1, len(USERS) + 5):
        create_user(TARGET_URL, f"perm-target-offset-{index}")
    target_users = {username: create_user(TARGET_URL, username) for username in reversed(USERS)}
    if any(source_users[name]["id"] == target_users[name]["id"] for name in USERS):
        raise ProbeFailure(
            "permission probe requires deliberately different source and target user IDs"
        )

    source_teams = create_structure(SOURCE_URL)
    accounts_before_transfer = target_account_names()
    target_teams = create_structure(TARGET_URL)
    seed_repository = seed_selected_repositories()
    target_control_before = git_refs(TARGET_URL, CONTROL_REPO)

    for username in ("perm-reader", "perm-late"):
        api(SOURCE_URL, "PUT", f"/teams/{source_teams['readers']['id']}/members/{username}")
    api(
        SOURCE_URL,
        "PUT",
        f"/teams/{source_teams['writers']['id']}/members/perm-writer",
    )

    mappings = {
        "perm-reader": add_mapped_member(
            "perm-reader", "read", target_teams["readers"], target_users
        ),
        "perm-writer": add_mapped_member(
            "perm-writer", "write", target_teams["writers"], target_users
        ),
    }
    for username in (
        "issue-author",
        "comment-author",
        "comment-author-b",
        "gitferry-importer",
    ):
        mapping, _ = resolve_mapping(username, target_users)
        mappings[username] = mapping
        for base_url in (SOURCE_URL, TARGET_URL):
            api(
                base_url,
                "PUT",
                f"/repos/{SELECTED_REPO}/collaborators/{username}",
                json_body={"permission": "write"},
            )

    accounts_after_structure = target_account_names()
    no_accounts_created_by_transfer = accounts_before_transfer == accounts_after_structure
    if not no_accounts_created_by_transfer:
        raise ProbeFailure("organization/team provisioning unexpectedly created user accounts")

    reader_auth = ("perm-reader", USER_PASSWORD)
    writer_auth = ("perm-writer", USER_PASSWORD)
    outsider_auth = ("perm-outsider", USER_PASSWORD)
    late_auth = ("perm-late", USER_PASSWORD)
    reader_url = authenticated_url(TARGET_URL, f"/{SELECTED_REPO}.git", reader_auth)
    writer_url = authenticated_url(TARGET_URL, f"/{SELECTED_REPO}.git", writer_auth)
    outsider_url = authenticated_url(TARGET_URL, f"/{SELECTED_REPO}.git", outsider_auth)
    late_url = authenticated_url(TARGET_URL, f"/{SELECTED_REPO}.git", late_auth)

    access = {
        "reader_can_read": run(["git", "ls-remote", reader_url], check=False).returncode == 0,
        "reader_push_denied": run(
            ["git", "push", reader_url, "HEAD:refs/heads/reader-must-not-exist"],
            cwd=seed_repository,
            check=False,
        ).returncode
        != 0,
        "writer_can_read": run(["git", "ls-remote", writer_url], check=False).returncode == 0,
        "writer_push_allowed": run(
            ["git", "push", writer_url, "HEAD:refs/heads/writer-permission-proof"],
            cwd=seed_repository,
            check=False,
        ).returncode
        == 0,
        "unmapped_same_login_account_denied": run(
            ["git", "ls-remote", outsider_url], check=False
        ).returncode
        != 0,
        "late_mapping_denied_before_assignment": run(
            ["git", "ls-remote", late_url], check=False
        ).returncode
        != 0,
    }

    mappings["perm-late"] = add_mapped_member(
        "perm-late", "read", target_teams["readers"], target_users
    )
    access["late_mapping_allowed_after_assignment"] = (
        run(["git", "ls-remote", late_url], check=False).returncode == 0
    )
    access["reader_branch_absent"] = "refs/heads/reader-must-not-exist" not in git_refs(
        TARGET_URL, SELECTED_REPO
    )
    access["writer_branch_present"] = "refs/heads/writer-permission-proof" in git_refs(
        TARGET_URL, SELECTED_REPO
    )
    if not all(access.values()):
        raise ProbeFailure(f"effective permission assertion failed: {access}")

    team_configuration = assert_team_configuration(source_teams, target_teams)
    target_control_after = git_refs(TARGET_URL, CONTROL_REPO)
    control_untouched = target_control_before == target_control_after
    if not control_untouched:
        raise ProbeFailure("unselected control repository refs changed")

    admin_flags = {name: user["is_admin"] for name, user in target_users.items()}
    if any(admin_flags.values()):
        raise ProbeFailure(f"functional users unexpectedly became administrators: {admin_flags}")

    owner_team = next(
        team for team in api(TARGET_URL, "GET", f"/orgs/{ORG}/teams") if team["name"] == "Owners"
    )
    owner_logins = tuple(
        sorted(
            user["login"] for user in api(TARGET_URL, "GET", f"/teams/{owner_team['id']}/members")
        )
    )
    if "gitferry-admin" not in owner_logins:
        raise ProbeFailure("technical target owner is missing from the organization")

    report = {
        "status": "passed",
        "interfaces": [
            "Gitea REST organization/team/repository/member endpoints",
            "Git smart HTTP with functional-user Basic authentication",
        ],
        "identity_scope": "synthetic mapping only; no LDAP or AD connection was exercised",
        "gitea_account_creation_requirement": (
            f"Gitea {PRIMARY_GITEA_VERSION} admin user creation requires an email; "
            "synthetic .invalid values were "
            "used only for account provisioning and never as mapping keys"
        ),
        "source_target_id_differences": {
            name: {"source": source_users[name]["id"], "target": target_users[name]["id"]}
            for name in USERS
        },
        "explicit_mappings": mappings,
        "open_mapping_conflicts": {
            "perm-outsider": (
                "same-login target account exists but no explicit mapping was configured"
            )
        },
        "provision_without_user_transfer": {
            "accounts_created_by_transfer": [],
            "target_accounts_unchanged_during_structure_transfer": no_accounts_created_by_transfer,
            "technical_owner_logins": owner_logins,
            "initially_open_mapping": "perm-late",
            "late_mapping_added_explicitly": True,
        },
        "team_configuration": team_configuration,
        "effective_git_access": access,
        "effective_permission_scope": (
            "repo.code read/write over Git smart HTTP; issue permissions and other Gitea units "
            "were configuration-compared but not inferred from the Git push result"
        ),
        "functional_user_admin_flags": admin_flags,
        "access_checks_used_administrator_credentials": False,
        "control_repository": {
            "selected": False,
            "refs_before": target_control_before,
            "refs_after": target_control_after,
            "untouched": control_untouched,
        },
    }
    return report, AccessContext(
        source_users=source_users,
        target_users=target_users,
        source_teams=source_teams,
        target_teams=target_teams,
        mappings=mappings,
    )
