# ADR 0012: Local plans reference the existing operation journal

Status: accepted under [Issue #9](https://github.com/hackepeter87/GitFerry/issues/9), integrated as `1b0bed5`.

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
It does not interrupt a POST or undo completed work. Shutdown signals the worker to yield at
the next safe boundary and waits up to two seconds. If the process exits while a remote call
is still in flight, recovery conservatively treats the executing operation as outcome unknown.
Confirmed progress remains resumable; shutdown does not promise remote cancellation.

The internal adapter boundary composes only GitFerry's supported operations. It is not a public
plugin protocol, arbitrary task runner, cross-machine queue or imported execution status. No
HTTP or CLI endpoint may expose the worker's internal `finish` transition as a success override.

## Scope of current evidence

The first implementation slice tests local plan persistence, content identity, transactions,
dependency validation, cancellation and real process interruption using a simulated remote
counter. It also adds reusable issue create/update operations and moves structure collision
checks before the first structure write. These tests are not real-Gitea evidence for the complete
background plan workflow.

The application planner, fixed Gitea/bundle composition, API/CLI boundaries and real-Gitea
restart/crash/lost-response probes are now implemented and locally exercised. The protected
server starts its worker using the same runtime store and paths. Issue #9 passed exact-head
review and hosted PR/main verification. The plan panel uses the same persisted state and supports reviewed
execution and draft creation from saved local selections. Chromium tests distinguish the real
worker with a simulated remote from the separate real-Gitea interruption proof. The existing
integration suite remains the regression baseline. Manifest v1/v2 versions and bytes do not
change; local schema migration 7 is independent of their transport versions.

## Application boundary

The fixed application planner derives journal IDs and dependency edges from selected
provisioning and metadata inputs; the HTTP interface must not accept arbitrary executable
callbacks or let a transport document select local credentials. The existing provisioning DTOs
and metadata origin/baseline records are the source of those intents. Account credentials for
metadata remain locally resolved reference aliases keyed by confirmed binding ID. Administrative
impersonation is not assumed: the existing evidence uses the actual mapped author's credentials.
The adapter reads the authenticated account and compare its native ID and login before any
write, and rechecks the target connection evidence against the reviewed plan.

All selected structure collisions, author prerequisites, supported metadata fields, bundle
integrity, expected target refs and available workspace must be checked before the first target
write. Label-content mutations are not hidden inside an issue PATCH; the current bounded issue
adapter rejects them. This preserves the separate label capability requirement in the roadmap.
The simulated unit adapters remain test scaffolding. The real target-only Linux probe now
executes the fixed application adapter and records remote acceptance before both interruption
points. It does not replace the independent manifest roundtrip evidence or complete UI acceptance.
