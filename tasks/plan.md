# Implementation plan: GitFerry iteration 1

Source specification: project-owner initial assignment, normalized in `docs/requirements.md`.

## Architecture decisions

- Modular monolith, SQLite control plane, filesystem payload plane.
- Contract-first blueprints with fail-closed validation.
- Risk-first integration probes before any migration-support promise.

## Task list

### Foundation
1. Repository policy, architecture, requirements, ADRs, and commands.
2. Blueprint schemas, examples, validator, API endpoint, and unit/API tests.
3. Backend container and Vue/TypeScript placeholder build.

### Feasibility
4. Pinned two-Gitea Compose environment and automated synthetic seeding.
5. Git-bundle integrity/offline-isolation probe.
6. Issue/comment field-fidelity probe.

### Verification
7. Run local quality gates and both integration probes.
8. Replace pending matrix entries with exact results and limitations.
9. Add GitHub Actions using the same Make targets.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| API cannot preserve fields | High | Field-level report; classify limitation, do not promise support |
| Docker/network differs in CI | Medium | Doctor check, fixed image digest, loopback ports, explicit timeouts |
| Offline test has hidden egress | High | Isolated internal network plus negative source/public probes |
| Test data collides across runs | Medium | Unique Compose project and full owned-resource teardown |

## Verification checkpoints

- Foundation: `make test` passes.
- Feasibility: `make test-integration` produces a report from fresh state.
- Complete: docs match executed evidence; no full migration claim.
