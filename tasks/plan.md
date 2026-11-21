# Delivery index and dependency boundaries

Public issues/milestones own scope and acceptance; the GitHub Project owns live status. This
index records the release sequence and actual delivery relationships, not a second progress log.
Use the [issue contract](../docs/issue-contract.md) and retain run evidence in Issues/Actions.
Each coherent outcome gets one PR with small commits, an explicit elapsed-time bound and a
resumable checkpoint. No automatic budget renewal or documentation-only evidence loop.

## Actual 0.2 delivery boundaries

| Outcome | Owning issue | Actual PRs |
|---|---|---|
| Persistent installed OCI application | #6 | #45 |
| Protected administration and API | #7 | #46 |
| Locally approved connections and TLS | #8 | #49 |
| Durable execution, restart and controlled reconciliation | #9 | #50 |
| Guided source-to-target desktop first import | #10 | #51–#58 |
| Secured dependency and Python contract | #47 | Existing #38 |
| Execution-ready issue contract and roadmap audit | #48 | Linked completion PR on the issue |
| Exact integrated candidate and milestone acceptance | #11 | Acceptance record links the above integrated deliveries |

The intermediate PRs for #10 are historical facts, not a prescribed pattern: #51 selection,
#52 package boundary, #53 source capture, #54 admission/plan integration, #55 export/download,
#56 upload, #57 local decisions, #58 full desktop/OCI acceptance. Future deliveries use complete
user outcomes rather than another PR for each technical layer. #11 requires #47 and #48 as
well as #6–#10; a completed UI issue does not silently close the whole milestone.

## Roadmap issue dependency index

`Depends on` points from the consuming issue to prerequisites. Acceptance issues aggregate
complete child outcomes. Existing product scope and release ordering are unchanged.

| Issue | Release | Outcome | Direct prerequisites |
|---|---|---|---|
| [#8](https://github.com/hackepeter87/GitFerry/issues/8) | 0.2.0 | Manage Gitea connection and TLS profiles without embedded secrets | #6, #7 |
| [#9](https://github.com/hackepeter87/GitFerry/issues/9) | 0.2.0 | Build a durable transfer plan and background execution service | #6, #7, #8 |
| [#10](https://github.com/hackepeter87/GitFerry/issues/10) | 0.2.0 | Deliver the guided first-import administration workflow | #7, #8, #9 |
| [#11](https://github.com/hackepeter87/GitFerry/issues/11) | 0.2.0 | Accept the 0.2.0 operable first-import flow | #6, #7, #8, #9, #10, #47, #48 |
| [#12](https://github.com/hackepeter87/GitFerry/issues/12) | 0.3.0 | Implement explicit identity mapping and conflict administration | #7, #8, #9, #10, #11 |
| [#13](https://github.com/hackepeter87/GitFerry/issues/13) | 0.3.0 | Provide a documented Gitea authentication-source bootstrap adapter | #7, #8, #11 |
| [#14](https://github.com/hackepeter87/GitFerry/issues/14) | 0.3.0 | Transfer organization, team, repository, and effective rights | #12, #13 |
| [#15](https://github.com/hackepeter87/GitFerry/issues/15) | 0.3.0 | Qualify real LDAP and Active Directory integration separately | #12, #13 |
| [#16](https://github.com/hackepeter87/GitFerry/issues/16) | 0.3.0 | Accept both 0.3.0 provisioning modes with real identities | #11, #12, #13, #14, #15 |
| [#17](https://github.com/hackepeter87/GitFerry/issues/17) | 0.4.0 | Inventory selected Gitea content and produce a complete transfer plan | #16 |
| [#18](https://github.com/hackepeter87/GitFerry/issues/18) | 0.4.0 | Qualify and transfer Git LFS and wiki repositories | #17 |
| [#19](https://github.com/hackepeter87/GitFerry/issues/19) | 0.4.0 | Transfer issue labels, attachments, and release assets | #17 |
| [#20](https://github.com/hackepeter87/GitFerry/issues/20) | 0.4.0 | Qualify pull requests, reviews, review comments, timelines, and reactions | #12, #17, #18 |
| [#21](https://github.com/hackepeter87/GitFerry/issues/21) | 0.4.0 | Accept the complete agreed 0.4.0 data scope | #16, #17, #18, #19, #20 |
| [#22](https://github.com/hackepeter87/GitFerry/issues/22) | 0.5.0 | Return Git repository changes through the portable transfer flow | #18, #21 |
| [#23](https://github.com/hackepeter87/GitFerry/issues/23) | 0.5.0 | Return supported metadata with stable origin and replica mappings | #21, #22 |
| [#24](https://github.com/hackepeter87/GitFerry/issues/24) | 0.5.0 | Unify return-transfer drift, conflicts, and unknown outcomes | #22, #23 |
| [#25](https://github.com/hackepeter87/GitFerry/issues/25) | 0.5.0 | Deliver the guided return and conflict-resolution UI | #7, #24 |
| [#26](https://github.com/hackepeter87/GitFerry/issues/26) | 0.5.0 | Accept the integrated 0.5.0 A-to-B-to-A workflow | #21, #22, #23, #24, #25 |
| [#27](https://github.com/hackepeter87/GitFerry/issues/27) | 0.6.0 | Add a signed outer transport envelope and local trust store | #26 |
| [#28](https://github.com/hackepeter87/GitFerry/issues/28) | 0.6.0 | Implement streaming storage, capacity controls, and package retention | #6, #27 |
| [#29](https://github.com/hackepeter87/GitFerry/issues/29) | 0.6.0 | Support cancellation and restart of complete transfer operations | #26, #28 |
| [#30](https://github.com/hackepeter87/GitFerry/issues/30) | 0.6.0 | Deliver backup, restore, and schema upgrade procedures | #27, #28, #29 |
| [#31](https://github.com/hackepeter87/GitFerry/issues/31) | 0.6.0 | Qualify an approximately 30 GB offline transfer | #27, #28, #29, #30 |
| [#32](https://github.com/hackepeter87/GitFerry/issues/32) | 0.6.0 | Accept the 0.6.0 hardened operating flow | #26, #27, #28, #29, #30, #31 |
| [#33](https://github.com/hackepeter87/GitFerry/issues/33) | 1.0.0 | Complete installation, administration, and operating documentation | #32 |
| [#34](https://github.com/hackepeter87/GitFerry/issues/34) | 1.0.0 | Perform full product workflow acceptance | #32, #33 |
| [#35](https://github.com/hackepeter87/GitFerry/issues/35) | 1.0.0 | Build and verify immutable 1.0 release-candidate artifacts | #34 |
| [#36](https://github.com/hackepeter87/GitFerry/issues/36) | 1.0.0 | Accept the 1.0 release candidate without publishing stable | #32, #33, #34, #35 |
| [#47](https://github.com/hackepeter87/GitFerry/issues/47) | 0.2.0 | Resolve pytest and setuptools delivery-chain alerts | #5 |
| [#48](https://github.com/hackepeter87/GitFerry/issues/48) | 0.2.0 | Make roadmap issues execution-ready and evidence-traceable | #5 |

## Explicit research and decision ownership

- #13/#15: documented authentication bootstrap and separate real LDAP/AD evidence. OpenLDAP
  cannot establish AD SID/group behavior; unavailable licensed/access prerequisites require a
  concrete alternatives-and-decision record without blocking unrelated authorized work.
- #17/#18: complete inventory followed by actual LFS/wiki offline first-import and return probes.
- #19: labels, attachments and releases require native relationships, payload and fidelity evidence.
- #20: PRs, reviews, inline comments, timelines and reactions require API feasibility before any
  completeness promise. Unsupported fields remain owned requirements/decisions, not deletions.
- #31: approximately 30 GB is a measured capacity qualification, not inferred from small fixtures.
- #36: technical candidate acceptance is authorized; stable publication remains a separate owner
  decision after this issue, not an implied consequence of a green milestone.

The audit checks required contract/metadata presence and consistency. Independent review checks
these dependencies for cycles, orphaned scope and duplicate ownership. Nothing here promotes a
future capability to implemented status; the capability matrix retains its evidence limits.

---

# Historical 0.1.0 foundation implementation

Source: `docs/roadmap.md` recommended next slice and the project-owner collaboration request.

## Overview

First establish the repository-scoped Codex role model. Then extract the already proven
organization/team/repository, explicit-identity and Git-bundle behavior into reusable application
services. The milestone ends only after the existing pinned Gitea integration environment proves
the bounded selected-repository flow and replay behavior. Complete the foundation by replacing the
coarse capability list with a field-level, issue-owned evidence contract and performing integrated
0.1.0 acceptance. It remains narrower than a general transfer engine.

## Architecture decisions

- Keep the main agent responsible for architecture, integration and milestone acceptance; use
  economical subagents only for bounded discovery and non-overlapping pattern-driven edits.
- Extend the existing origin, mapping and operations journal rather than adding a second state or
  replay mechanism.
- Model organizations, teams and repositories as durable origins/replicas. Team-repository,
  membership and bundle writes reference an already mapped parent resource and remain journaled.
- Extract native Git bundle handling as a narrow filesystem/subprocess service with bounded paths
  and digest verification. Do not build package orchestration or a general job engine.
- Treat existing target objects without a GitFerry mapping as collisions; never adopt them by name.

## Task list

### Phase 1: durable collaboration

- [x] Add repository-scoped main, explorer, mechanic and reviewer model configuration.
- [x] Add compact delegation rules and operating documentation.
- [x] Run a bounded explorer probe and distinguish configured from runtime-confirmed model choice.

### Phase 2: reusable provisioning core

- [x] Extend durable origins and journal operation types for organization, team and repository writes.
- [x] Extract verified Git bundle creation/import primitives with focused tests.
- [x] Add a bounded Gitea provisioning client/service that consumes explicit confirmed identity bindings.
- [x] Add focused unit tests for idempotency, collisions, role constraints and outcome-unknown blocking.

### Phase 3: real-instance acceptance

- [x] Route the selected repository structure, membership and bundle steps through the application services.
- [x] Prove reader/writer/outsider behavior and unchanged control objects on Gitea 1.27.2.
- [x] Prove process-restart replay creates no duplicate structure or metadata.
- [x] Run all repository gates and update testing, capability matrix and roadmap with exact evidence.

### Phase 4: product capability contract

- [x] Map agreed product classes to current code, tests and real-Gitea reports.
- [x] Separate implementation, first-import and return status with a fixed vocabulary.
- [x] Assign every row one target release and owning GitHub issue.
- [x] Add a machine-checked completeness, link, pin and known-limit contract.
- [x] Obtain independent review, hosted CI and integrated-main verification for Issue #3.

### Phase 5: integrated 0.1.0 acceptance

- [x] Audit Issues #1–#4, their PR/check evidence and the active main ruleset.
- [x] Verify the exact integrated capability-matrix commit in hosted Linux CI.
- [x] Run a context-free resume exercise and recover its blocked GitHub read without duplication.
- [x] Classify local, hosted, configured-only, limited and untested evidence in a milestone report.
- [x] Obtain independent final review, merge the report and verify the resulting main commit.

## Verification checkpoints

- Core: focused state, bundle and provisioning unit tests pass with Ruff and MyPy.
- Integration: the fresh two-Gitea run passes its mandatory report contract and scoped cleanup.
- Complete: documentation claims only the fields and permissions observed on the pinned Gitea run.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Extending object types changes existing SQLite constraints | High | Versioned migration, upgrade test, foreign-key check and replay regressions |
| Existing same-name target structure is adopted accidentally | High | Fail closed unless an immutable local mapping already identifies it |
| Bundle extraction grows into a transfer engine | Medium | One repository, explicit paths, digest/ref verification, no scheduling or package format |
| Multiple writers bypass journal guarantees | High | Reuse the existing executor lock, lease and outcome-unknown behavior |
| Probe-only behavior is reported as product support | High | Update capability claims only after the real Gitea application service path passes |

## Open questions

None for this bounded slice. Unsupported permission dimensions, historical timestamps, productive
LDAP/AD, signatures, general resume and a full transfer engine remain explicitly deferred.
