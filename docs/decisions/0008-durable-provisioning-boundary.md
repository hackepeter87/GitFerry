# ADR 0008: Durable provisioning boundary

Status: accepted for Issue #4's bounded milestone

Date: 2026-11-20

## Context

Earlier real-instance probes proved Git bundles, organization/team permissions, explicit identity
bindings, and durable issue/comment writes separately. A guided application flow needs one
reusable service that composes those pieces without introducing a second journal or holding a
SQLite write transaction across a Gitea request. SQLite and Gitea cannot commit atomically, so an
interrupted remote write must remain distinguishable from work that was never sent.

## Decision

The reusable application service is the execution boundary used by the integration worker and
intended for later CLI and UI callers. It provisions only the tested organization, team, selected
repository, membership, and bundle scope. SQLite schema v4 stores local origins, mappings, journal
events, leases, and outcomes; it is not a transport artifact.

The service follows these rules:

- Prepare and durably commit the intent before calling Gitea.
- Perform the remote call outside the SQLite transaction.
- Atomically commit target mapping and `confirmed` after a usable response.
- Re-read a confirmed organization, repository, or team before dependent writes and require its
  live stable ID and supported properties to match the immutable local mapping.
- Fail closed on an existing target collision without an immutable mapping.
- Mark dependent operations `blocked_parent` until their parent mapping is confirmed.
- Verify the complete expected team/unit mapping; this proof covers `repo.code` and `repo.issues`.
- Keep credentials and secret values outside SQLite, plans, reports, and bundles.
- On hard exit or lost response, retain `outcome_unknown`, halt replay, and require explicit
  evidence-based reconciliation; a new package identity must not bypass the halt.

## Consequences

Confirmed replay is safe for the bounded flow, while ambiguous remote outcomes are intentionally
not auto-reconciled. This is not a general exactly-once guarantee, distributed executor, LDAP/AD
integration, or complete permission model. The accepted evidence is limited to the pinned Gitea
1.27.2 integration run and synthetic identities.

## Alternatives considered

- Adopt target objects by matching names: rejected because a name is not an origin or identity
  proof and could redirect rights to unrelated local structure.
- Keep structure provisioning in the integration harness: rejected because CLI, worker, and future
  UI must share the same application behavior.
- Hold one SQLite transaction over the remote request: rejected because it neither makes Gitea
  atomic nor preserves the short local transaction boundary.
- Retry an uncertain create automatically: rejected because a local uniqueness constraint cannot
  prevent duplicate external Gitea objects.

## Evidence

See [`verification-durable-provisioning.md`](../verification-durable-provisioning.md) and the
generated run report `integration-results/report.json` (run `f6765a2dec4a436a`).
