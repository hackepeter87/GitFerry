# Identity, permission, and metadata roundtrip verification

## Evidence boundary

The probe ran on 2026-11-20 against two fresh Gitea 1.27.2 instances using image digest
`sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`.
It used only synthetic users and documented REST/Git interfaces. The raw field-level evidence is
`integration-results/report.json`; this generated file is not committed by default.

The following four concepts are intentionally separate:

1. **External identity**: realm, login, external ID/SID, and OU path. Email is optional and is not a
   key.
2. **Local Gitea account**: instance-local login and numeric ID.
3. **Historical author binding**: the local account used for the native `Issue.user` or
   `Comment.user` field, plus separately stored origin information.
4. **Current permission assignment**: an explicit repository/team role granted to the resolved
   local account. Resolving an identity alone grants nothing.

Gitea's `POST /admin/users` required an email for synthetic account creation. The probe supplied
`.invalid` addresses for that provisioning prerequisite only. Missing directory email attributes
still resolved because email was never consulted by the mapping model.

This is not LDAP or Active Directory evidence. The realms, SIDs, and OU paths are synthetic model
inputs; no directory server was contacted.

## Identity mapping cases

| Case | Input and mapping | Expected | Observed | Status / limitation |
|---|---|---|---|---|
| Unique | Full realm + login + SID + OU selector; existing non-admin target account | Resolve exactly one account, no implicit grant | Resolved with missing email; author and permission records required separate calls | `passed` |
| Same login, other realm | Same `sam` login, different realm/SID/OU, no matching rule | No name-based match | `unmapped` | `passed` |
| Ambiguous | Two explicit rules match one full external key | Visible conflict | `ambiguous` with conflict text | `passed` |
| Existing binding collision | Target account already bound to another external key | No takeover | `collision` | `passed` |
| Privilege expansion | Explicit target is an administrator | Fail closed | `collision` | `passed`; administrator mapping needs a separately designed policy |
| Missing mapping | Existing same-login target account but no rule | No account creation and no rights | Account remained present but could not read the private repository | `passed` |

All seven practical source-to-target user mappings used different numeric IDs. Concrete IDs and
the full synthetic realm/SID/OU records are in the report.

## Organization, team, and effective permission probe

### Starting data and interfaces

- organization `access-lab`, private selected repository `selected-private`, and private control
  repository `control-private`;
- teams `readers` and `writers`, with `repo.code`/`repo.issues` configuration copied through the
  organization, team, member, and team-repository REST endpoints;
- `perm-reader`, `perm-writer`, `perm-outsider`, and initially unmapped `perm-late` functional
  users;
- a separate `gitferry-admin` target owner for setup;
- Git smart HTTP authenticated as each functional user for effective code-access checks.

### Observed results

| Assertion | Observed | Status / limitation |
|---|---|---|
| Reader clones selected private repository | Allowed | `passed` |
| Reader pushes a new branch | Denied; branch absent | `passed` |
| Writer clones and pushes `writer-permission-proof` | Allowed; branch present | `passed` |
| Unmapped same-login account reads selected repository | Denied | `passed` |
| Initially unmapped late user | Denied before explicit mapping; allowed after reader-team assignment | `passed` |
| Control repository | No team assignment; refs identical before/after | `passed` |
| Functional administrative roles | Every functional account reported `is_admin: false` | `passed` |

The runtime access result proves only the tested `repo.code` read/write behavior. Other units were
configuration-compared, but a successful Git push is not evidence for complete Issues, Pulls,
Wiki, Actions, Packages, or administration permission fidelity.

Structure provisioning created no functional accounts. Gitea correctly created an `Owners` team
with technical owner `gitferry-admin`; the organization is therefore not described as userless.

## Native author verification

`issue-author` created the issue, `comment-author` created its comment, and
`gitferry-importer` performed field updates. On both instances, issue and comment `user.id` and
`user.login` matched the explicitly mapped local accounts, whose source and target numeric IDs
were different. Neither native author became the import user. Origin realm/SID/OU and original
timestamps were stored separately in the report.

The mechanism was not administrator impersonation: create calls were authenticated directly as
the mapped synthetic target accounts. This requires those accounts to exist and GitFerry to have
an approved way to act as them. Production credential delegation or administrative `sudo` policy
is not designed or claimed here.

## Metadata roundtrip cases

### Case A: existing issue on central system A

- **Mapping:** explicit A global issue ID + repository issue index + comment ID to their B IDs.
- **Change:** B changed the issue body; the technical importer patched the mapped existing issue on
  A.
- **Observed:** A issue count did not increase, global ID stayed the same, body returned, native
  author and `created_at` stayed unchanged, and A `updated_at` changed.
- **Timestamp limitation:** A generated a new update time; B's exact `updated_at` was not restored.
- **Unrelated data:** a simultaneous description change in the unselected control repository did
  not block the selected issue return.

### Case B: new issue and comment from B

- **Mapping:** explicit origin key `B + repository + global issue ID`, with separate global issue
  ID, repository issue index, comment ID, and destination IDs.
- **Observed:** content, native mapped authors, and issue/comment relation were retained. A assigned
  new native IDs and current native creation times. Original B times remained only in origin
  information.
- **Repeat:** a second application found the stored origin key and performed no create call; counts
  remained unchanged.

### Case C: central selected-data conflict

The baseline covers exactly issue `title`, `body`, `state`, and sorted label names. After export,
both B and the mapped A issue changed. The selected-field comparison detected A drift, returned
`blocked-conflict`, and left the central change untouched. It does not use a global instance
timestamp and does not claim to detect changes to fields outside this allowlist.

## Timestamp schemas and server-side migration route

Both live `/swagger.v1.json` documents had the same SHA-256 digest. In their request schemas:

- `CreateIssueOption` has no `created_at` or native `user` field;
- `CreateIssueCommentOption` contains only `body` and has no `created_at` or native `user` field.

The `Issue` and `Comment` response schemas expose native authors plus `created_at` and `updated_at`.
This confirms that response fields are not automatically valid create inputs.

A separate `POST /api/v1/repos/migrate` experiment used `service=gitea`, live target-server access
to the synthetic source, and a test-only migration allowlist. It preserved issue/comment content
and `created_at`, but both native authors became `gitferry-admin`; `original_author` and
`original_author_id` remained empty/zero. A second import to the same repository name returned
409.

Consequently, the server migration route is a candidate for a bounded first repository import
when live source access is acceptable. It is not offline package import, does not restore native
authors in this scenario, and cannot update an already mapped object or replace roundtrip
baselines and conflict handling.
