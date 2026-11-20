# Implementation plan: 0.1.0 foundation completion

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
- [ ] Obtain independent final review, merge the report and verify the resulting main commit.

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
