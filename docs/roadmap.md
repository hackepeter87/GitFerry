# Prioritized roadmap

## Recommended next slice

Build one cohesive bounded provisioning flow that composes the already proven Git bundle,
organization/team/repository structure, explicit identity mappings, functional permission checks,
and supported issue/comment metadata. Acceptance: one selected synthetic repository is provisioned
through shared application services, every native write still uses the durable journal and local
trust decisions, and a repeat run creates no duplicate structure or metadata. This is not yet a
general transfer engine.

The consolidated integration runner is implemented and passed locally with Linux/arm64 service
containers, deterministic interruption evidence, required-report validation, controlled-failure
diagnostics, and scoped cleanup. The same entry point is wired to an `ubuntu-24.04` GitHub Actions
job. Because this workspace was neither committed nor pushed, the hosted Linux run remains an
explicit open verification step; do not describe the workflow definition as a completed CI run.

The portable issue-manifest v1 slice is complete: schema, examples, CLI, SQLite migration,
transactional admission, isolated A → B → A execution, restart replay, repackaging, and drift
blocking have real Gitea 1.27.2 evidence.

The bounded manifest-v2 normal-comment slice is also complete: v1 compatibility, typed parent
origins, mapped authors, body baselines, parent-first execution, conflict/restart handling, three
separate-state roundtrip cases, and both hard-exit and lost-response probes have real Gitea 1.27.2
evidence. Parent moves, deletion, review comments, and timelines remain outside this slice.

## P0: Close feasibility evidence

1. Execute the prepared expanded two-instance job on a hosted Linux runner after an explicitly
   approved commit/push. Acceptance: two clean rebuilds retain the same semantic required checks,
   publish reports, and leave no run resources.
2. Add pull request and review probes. Acceptance: every author/time/state/relation field is classified.
3. Probe LFS, wiki, attachments, and release assets independently. Acceptance: no capability inherits ordinary-Git results.

## P1: Safe package and job core

1. Add a signed envelope around immutable manifest bytes and safe streaming package IO. Acceptance:
   traversal, link escape, size/count limits, tampering, signer mismatch, and signature failures are
   tested. Do not change transport-manifest v1 bytes in place.
2. Extend the bounded SQLite operation journal only when another proven write type needs it. The
   current issue-create restart/crash slice is complete; general jobs, distributed execution, and
   exactly-once semantics remain out of scope.
3. Implement disk preflight and a separate 30 GB benchmark. Acceptance: runtime, peak RSS, and disk use are recorded.

## P2: Metadata and identity slices

1. Persist the verified organization/team/repository plan and explicit collision outcomes.
2. Persist identity candidates keyed by realm, login, external ID, and OU, with manual ambiguity resolution.
3. Reuse the verified functional-user permission checks as post-provision validation.
4. Add an LDAP integration test environment, followed by a separate AD-specific qualification.

## P3: Product workflows

1. Bootstrap export/import.
2. Full-by-default provision with selective override.
3. Roundtrip baselines, drift conflicts, and unrelated-project invariants.
4. CLI and guided web UI over the same services.

Each item must update `docs/capability-matrix.md` with a real test result before a support claim is made.
