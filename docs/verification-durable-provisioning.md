# Durable provisioning verification

- Run: `f6765a2dec4a436a`
- Code state: clean commit `209064243f1c3b2130e9fb337ac551135368dee6`
- Date: 2026-11-20
- Status: passed locally; hosted CI and integrated-main verification pending

## Inputs and environment

- Gitea `1.27.2`, digest
  `sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`.
- Darwin/arm64 host; Docker Server Linux/arm64 29.9.3; no emulation observed.
- Reusable GitFerry application service, separate target-side SQLite state, and a file bundle.
- Synthetic users and repositories only; no credentials in the state, plan, or report.

## Mapping and expected result

The plan mapped one source organization, one selected repository, the `readers` and `writers`
teams, their memberships and repository assignments, and the bundle refs. Gitea creates the
technically required `Owners` team when it creates an organization; GitFerry did not model that as
a transferred functional team. The expected result was nine journaled operations with one
observed external effect each, no account creation, exact refs, selected permissions, an unchanged
control repository, and safe replay.

## Observed result

| Assertion | Expected | Observed | Status |
|---|---|---|---|
| Structure | 1 organization, 1 repository, 2 planned teams plus required `Owners` | Counts matched | passed |
| Operations | All 9 confirm | All 9 confirmed | passed |
| Refs | Exact branch and annotated-tag names/object IDs | Matched; replay changed no refs | passed |
| Permissions | Reader clone/no push; writer clone/push; outsider denied | Matched over Git smart HTTP | passed |
| Accounts | No user transfer | No accounts created; technical owner remained separate | passed |
| Control | Unselected repository unchanged | Unchanged | passed |
| Replay | No duplicate structure/effect | Second process created no structure | passed |
| Crash/lost response | Halt safely; no retry POST | `outcome_unknown`, one executing event, no second POST | passed |
| Repackaging | Cannot bypass unknown | New package remained blocked | passed |

The team map explicitly configured `repo.code` and `repo.issues`; no broader effective-permission
claim is made. The live aggregate permission is `none` under the explicit per-unit map.

## Limits and test mapping

The proof does not establish general exactly-once behavior, distributed locking, automatic
reconciliation, LDAP/AD support, or a manifest v1/v2 change. Historical timestamps and the
comment PATCH race remain unchanged. The integration evidence maps to
`tests/integration/provisioning_probe.py`, with the 18/18 mandatory checks recorded by the run
report. Unit coverage supplies schema v4 migration, collision, parent dependency, journal, and
unknown-outcome transition checks. It also rejects a same-name repository whose live stable ID no
longer matches the confirmed mapping before any dependent permission write. Generated field-level
details remain in `integration-results/report.json`.
