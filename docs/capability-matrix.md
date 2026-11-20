# Gitea capability matrix

Evidence target: Gitea 1.27.2, image digest `sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`.

| Capability | Status | Evidence / limitation |
|---|---|---|
| Versioned blueprint validation | supported | Schemas require explicit realm/login/external-ID/OU mapping selectors; only `gitferry.io/v1` and known kinds accepted |
| Secret-reference-only blueprints | supported | Unknown/plaintext secret fields fail closed; examples contain references only |
| Ordinary Git refs/history via bundle | supported | Real 1.27.2 source→target probe matched two branches and an annotated tag by object ID |
| Offline import isolation | supported | Importer reached target only; source DNS/service and public IPv4 probe failed |
| Corrupt bundle rejection | supported with documented limitations | SHA-256 and clone detected footer corruption; `git bundle verify` alone did not |
| Issue title/body/state/label | supported with documented limitations | Fields matched for one issue; broader histories and transitions are untested |
| Explicit identity resolution | supported with documented limitations | Unit tests cover equal login across realms, different SID/OU, missing email, ambiguity, existing binding, admin collision, and unmapped users; no LDAP/AD server was used |
| Provisioning without user transfer | supported with documented limitations | Structure probe created no functional accounts and granted no rights to an unmapped same-login account; durable provisioning workflow is not implemented |
| Issue/comment author fidelity | supported with documented limitations | REST create calls authenticated as distinct mapped target accounts preserved native author IDs/logins; requires existing accounts and an approved act-as mechanism |
| Issue/comment timestamp fidelity | supported with documented limitations | Regular create cannot restore native `created_at`; origin time is retained separately. Server repository migration preserved `created_at` but lost native author fidelity |
| Durable instance/realm/identity state | supported with documented limitations | SQLite schema v3 persists operator-registered installation markers, realms, immutable explicit bindings, and admitted transport claims; no automatic Gitea installation ID or LDAP/AD discovery is claimed |
| Source→target object ID mapping | supported with documented limitations | SQLite persists origin instance/repository/type/global ID separately from issue index/comment relation; mappings are unique per target instance and cannot be implicitly redirected |
| Versioned selected baselines | supported with documented limitations | Immutable baseline, desired state, last observation, and comparison ruleset/version survived real process restarts; only title/body/state/sorted labels are covered |
| Durable operation journal | supported with documented limitations | Prepared intent hash, parent-blocked/executing/confirmed/outcome-unknown state, target ID, events, and conflicts are persisted without secrets for the bounded issue and normal-comment operations |
| Confirmed restart replay | supported with documented limitations | Real Gitea create completed once; a new Python process with the same file database treated replay as confirmed no-op and created no second issue |
| Unknown-outcome restart blocking | supported with documented limitations | Hard process exit after successful Gitea POST and a discarded successful response both became `outcome_unknown`; replay issued no second POST. Explicit stable-ID reconciliation is manual |
| Portable manifest v1 validation | supported with documented limitations | Local Draft 2020-12 schema, bounded UTF-8 JSON parser, duplicate-key rejection, exact-byte SHA-256 sidecar, safe relative payload paths, immutable manifest IDs, and transactional admission are covered; v1 carries issues only |
| Portable manifest v2 comments | supported with documented limitations | Separate local v2 schema adds normal `issue_comment`, qualified author, body comparison facts, and typed `parent_issue_origin`; v1 examples remain accepted with identical bytes/digests |
| Separate-state file roundtrip | supported with documented limitations | Real 1.27.2 A→B→A issue probe used two SQLite named volumes and explicit file copies; each GitFerry worker was network-isolated from the other Gitea and had no other state volume |
| Portable origin/replica continuity | supported with documented limitations | A origin survived the B replica and return; a B-born issue retained B origin on A. Repackaging under a new manifest ID reused origins and confirmed operations without duplication |
| Manifest trust boundary | supported with documented limitations | Identity relationships remain unapproved claims; imported success/approval fields are schema-rejected; credentials, local operations, events, locks, leases, and manual decisions are not transported |
| Manifest authenticated provenance | not supported | SHA-256 checks byte integrity and consistency only; no signature or encryption is implemented |
| Single local writer | supported with documented limitations | OS file lock plus SQLite lease blocked a second process while the first was in a real Gitea call; only one local shared volume/host is covered |
| Organizations/teams | supported with documented limitations | Real 1.27.2 probe recreated one organization, read/write teams, unit configuration, memberships, and only the selected repository assignment |
| Effective permissions | supported with documented limitations | Functional-user Git HTTP proved reader clone/denied push, writer clone/allowed push, and denied unmapped user; only `repo.code` effective access is claimed |
| Existing issue roundtrip | supported with documented limitations | Explicit mapping updated the existing A issue without duplication, preserved author/created_at, and generated a new A updated_at; not a general engine |
| File-mediated existing/new issue return | supported with documented limitations | Portable return manifest updated the mapped A issue and created one B-origin issue on A; same-manifest restart replay and new-envelope replay created no duplicates |
| New issue/comment return and replay | supported with documented limitations | New B objects were created once on A with mapped authors and relation; stored origin key made the bounded replay a no-op |
| File-mediated comment create/update | supported with documented limitations | Real v2 A→B→A probe updated one A comment in place, created a B comment on an A issue, and created a B issue plus child listed first in the file; replay and repackaging created no duplicates |
| Comment parent verification | supported with documented limitations | Admission uses stable issue origin; execution verifies mapped target through the complete expected-issue comment list. Missing/wrong parent blocks without PATCH or replacement create; parent changes/deletes are unsupported |
| Comment restart safety | supported with documented limitations | Real Gitea comment POST followed by hard process exit and by lost response each became `outcome_unknown`; restarted workers sent no second POST and retained no invented target ID |
| Reproducible integration entry point | supported with documented limitations | `make test-integration` creates a unique Compose project, fresh scoped volumes, two isolated state stores, performs the file roundtrip, validates a required-evidence catalog, writes platform/image/code-state facts, and fails on missing evidence or cleanup failure. Passed locally with Linux/arm64 containers on a Darwin/arm64 Docker host; a native Linux host run remains CI evidence |
| Deterministic interruption synchronization | supported with documented limitations | Hard-exit and lost-response probes fsync test-only evidence only after Gitea returned the created object, then a new process verifies the live issue/comment, parent, author, and content while GitFerry still holds `outcome_unknown` without a target ID or second POST |
| Integration failure diagnostics and scoped cleanup | supported with documented limitations | Controlled exit 86 produced a failed report, preserved Compose diagnostics, and removed only the run-labeled containers, networks, volumes, and run image; diagnostic publication on a hosted runner remains unexecuted |
| Linux GitHub Actions integration workflow | not checked | `ubuntu-24.04` job is prepared with the same entry point, run-specific names, bounded runtime, `always()` artifacts and cleanup. The current uncommitted code was not pushed, so no hosted Actions run is claimed |
| Conditional comment update | not supported | Live Gitea 1.27.2 `EditIssueCommentOption` exposes only `body`; read-before-PATCH detects observed drift but cannot atomically exclude a concurrent foreign writer |
| Selected-data drift conflict | supported with documented limitations | Title/body/state/sorted-label baseline blocked overwrite; unselected control-repo change did not block; other fields are outside the comparison |
| Gitea server repository migration | supported with documented limitations | `POST /repos/migrate` preserved content and created_at for first import, but used technical admin as native author, required live source access, and replay returned 409 |
| Pull requests/reviews | not checked | No support claim |
| LFS | not checked | Separate artifact test required |
| Wikis | not checked | Separate repository test required |
| Attachments/release assets | not checked | Separate binary test required |
| LDAP | not checked | Synthetic realm/SID/OU tests are not directory integration evidence |
| Active Directory | not checked | AD-specific environment required |
| General restart/resume/exactly-once | not supported | Bounded confirmed replay and safe unknown-outcome halt are proven; no general engine, distributed lock, automatic reconciliation, or exactly-once guarantee exists |
| 30 GB throughput | not checked | Qualification test deferred |
| GitFerry administrative authentication | not checked | Iteration-1 API skeleton has no authentication and is not production-ready |

Status vocabulary: **supported**, **supported with documented limitations**, **not supported**, **not checked**. Environment failures are reported separately as **blocked**, never converted to support. Results above come from the bounded iteration-1 probe, not a general migration engine.
