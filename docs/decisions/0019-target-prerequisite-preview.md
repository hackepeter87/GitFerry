# ADR 0019: Target prerequisite preview

## Status

Accepted for the bounded first-import workflow. This decision does not expand the supported
content inventory or claim a complete 0.2 release.

## Context

Administrators need to inspect target-side prerequisites and collisions before starting a reviewed
plan. A preview must not become an authorization shortcut, race with the executor, or hide an
unknown remote write. The first-import UI therefore needs a durable, digest-specific review state
separate from the execution plan and operation journal.

## Decision

- Store preview records in the separate SQLite `plan_previews` table. A request is associated with
  the exact plan digest and a monotonically increasing generation.
- SQLite migration 11→12 creates this table with a foreign key to the plan, a partial unique index
  allowing only one active (`queued`/`checking`) preview per plan, and durable status/reason/scope
  fields. If the process restarts while a preview is `checking`, the worker resumes it from the
  durable row; `GET /api/v1/plans/{id}` remains read-only and reports the state.
- Expose the read-only preview through `POST /api/v1/plans/{id}/preview` and the CLI
  `plans preview --plan-id ID --digest DIGEST`. Preview status is one of `queued`, `checking`,
  `passed`, `blocked`, or `failed`.
- Require the UI queue action to provide the exact passed preview generation for the current plan
  digest. Keep the legacy queue path for compatibility where the caller intentionally omits the
  optional generation; it does not weaken the executor's own preflight.
- Run preview and execution under the same local executor lock. Preview may perform bounded,
  read-only remote checks and may persist local operation/conflict state through the existing
  provisioning preflight. It may block on authority changes, structure (including collisions),
  permissions, storage, or missing author prerequisites. It never performs a target write or
  persists raw remote response text.
- Always repeat prerequisite checks in the executor immediately before writes. A passed preview is
  review evidence, not a guarantee against changes by other writers.

## Replay and recovery

Reads are safe to replay when their evidence remains valid. A confirmed write is replayed as a
no-op. If a remote write may have succeeded but local confirmation is missing, the operation stays
`outcome_unknown` and blocks both preview and execution until explicit reconciliation; no automatic
POST retry is permitted. This preserves the existing journal boundary without claiming general
exactly-once semantics.

## Consequences and limits

The UI can show a durable queued/checking/pass/blocked/fail preview and prevent stale evidence
from starting a changed plan. The preview does not provide atomicity, timestamp restoration, or an
atomic PATCH contract. Inventory completeness, unsupported content classes, and the existing
fidelity limitations remain governed by the capability matrix and manifest versions.
