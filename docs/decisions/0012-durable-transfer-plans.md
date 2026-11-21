# ADR 0012: Local plans reference the existing operation journal

Status: implementation in progress under [Issue #9](https://github.com/hackepeter87/GitFerry/issues/9).

## Context

The structure, Git bundle and metadata probes already use stable origins, explicit bindings,
selected baselines and an operations journal. Application requests need a durable lifecycle
independent of the browser without introducing another identity store or remote-write journal.

## Decision

SQLite schema 7 adds immutable plan documents, ordered references to existing operations, and
local plan events. A plan's SHA-256 digest covers its version, selected inventory, explicit round,
target profile revision and approved evidence, operation intents, local bindings, original
comparison facts, dependency graph and required workspace estimate. Mutable observations and
execution statuses are separate. Re-reading a target cannot replace the original comparison
facts or silently change the plan. Equivalent dependency ordering produces the same plan.

Queueing confirms the exact digest and revalidates local profile approval. A server-owned
worker uses the existing `OperationExecutor` file lock and SQLite lease, shared with CLI writers.
External I/O runs outside SQLite write transactions. Full selected-data preflight precedes any
write; individual domain operations retain their immediate identity/drift checks. A remote
system can still change after a read, including the documented comment-PATCH race.

The lifecycle is `draft → queued → preflighting → running → confirmed`. Failure paths end in
`blocked`, `failed` or `cancelled`. Requeue is explicit. A plan cannot become confirmed until
every referenced journal operation is confirmed. A new plan or round cannot bypass an uncertain
or conflicted operation for the same origin and target. These states never imply external
exactly-once execution.

On process restart the existing executor recovers interrupted writes as `outcome_unknown`.
An abandoned plan re-enters preflight; confirmed operations are reused and uncertainty blocks
further writes. Cancellation is a durable request honored before the next external operation.
It does not interrupt a POST or undo completed work. Graceful worker shutdown yields at the same
safe boundary and leaves the plan resumable. A hard exit retains the existing journal semantics.

The internal adapter boundary composes only GitFerry's supported operations. It is not a public
plugin protocol, arbitrary task runner, cross-machine queue or imported execution status. No
HTTP or CLI endpoint may expose the worker's internal `finish` transition as a success override.

## Scope of current evidence

The first implementation slice tests local plan persistence, content identity, transactions,
dependency validation, cancellation and real process interruption using a simulated remote
counter. It also adds reusable issue create/update operations and moves structure collision
checks before the first structure write. These tests are not real-Gitea evidence for the complete
background plan workflow.

Issue #9 remains open until the application planner, fixed Gitea/bundle composition, API/CLI/UI
boundaries, and real-Gitea restart/disconnect probes are wired and verified. The existing
integration suite remains the regression baseline. Manifest v1/v2 versions and bytes do not
change; local schema migration 7 is independent of their transport versions.

## Next application boundary

The fixed application planner must derive journal IDs and dependency edges from selected
provisioning and metadata inputs; the HTTP interface must not accept arbitrary executable
callbacks or let a transport document select local credentials. The existing provisioning DTOs
and metadata origin/baseline records are the source of those intents. Account credentials for
metadata remain locally resolved reference aliases keyed by confirmed binding ID. Administrative
impersonation is not assumed: the existing evidence uses the actual mapped author's credentials.
The adapter must read the authenticated account and compare its native ID and login before any
write, and must recheck the target connection evidence against the reviewed plan.

All selected structure collisions, author prerequisites, supported metadata fields, bundle
integrity, expected target refs and available workspace must be checked before the first target
write. Label-content mutations are not hidden inside an issue PATCH; the current bounded issue
adapter rejects them. This preserves the separate label capability requirement in the roadmap.
The new runner's unit adapter is internal test scaffolding, not a production migration claim.
