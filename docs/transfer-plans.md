# Durable transfer plans

The local application planner composes the existing organization, repository, team, Git-bundle,
issue and normal-comment operations. It does not introduce another mapping store or journal.
`PlansPanel` reviews, previews, starts, refreshes and cancels local plans, and can prepare another
draft from a saved selection without editing JSON. The first-import UI reaches it through source
selection, source export/download, package intake, and explicit local sender/realm/identity
decisions. This is still not a complete inventory of a source instance or a replacement for
transport-manifest validation.

## Local authority and execution

A plan records its target profile revision and approved connection evidence, selected intents,
original comparison facts, dependencies and a digest. Preparation never writes to Gitea.
Queueing requires the exact reviewed digest. The managed server owns the background worker;
closing an HTTP connection does not cancel queued work. The CLI shares the same executor lock.
Only one local writer can perform remote operations at a time.

Blocked preflight reports identify selection, storage, connection/CA, administrator, structure
(including target collisions), author credential, author identity, metadata, bundle, or journal
checks. These are closed diagnostic categories, shared by API/CLI/UI; remote response bodies and
exception strings are not persisted.

## Read-only prerequisite preview

`POST /api/v1/plans/{id}/preview` with body `{"digest":"<reviewed digest>"}` queues a read-only
target prerequisite check. Results live in the
separate SQLite `plan_previews` table and never authorize execution by themselves. A preview moves
through `queued`, `checking`, `passed`, `blocked`, or `failed`; blocked results include a closed
reason and safe scope. The UI requires a passed preview for the exact current plan digest and
generation. A new request creates a new generation, so stale evidence cannot unlock a changed
plan. The executor performs the same prerequisite checks again before writes.

Preview reads may prepare or block based on local journal state and remote observations; they do
not perform target writes. Local authority/profile changes, collisions, missing accounts or
permissions, unavailable storage, and unknown journal outcomes remain blockers. Preview and
execution share the executor lock so a preview cannot overlap the write executor.

Before any target mutation, the fixed Gitea adapter checks the approved HTTPS connection,
separate technical administrator, every selected structure collision, actual author accounts,
explicitly planned or existing repository access, selected metadata baselines and parents,
bundle integrity, target refs where the repository already exists, and available local space.
Each metadata author needs a local token-file reference for the explicitly bound target account.
There is no fallback to the issue author or administrator for comments. These references are
local configuration, not authority imported from a package.

New repository bundle publication is bounded to empty targets. Confirmed copies must retain
the expected refs. Git publication uses atomic ref creation with absence preconditions and
checks the mapped repository ID; it does not use a blind mirror push. Historical native create
timestamps remain new target timestamps. Selected label changes require a separate supported
operation and are blocked by this planner; labels have not been removed from the product scope.

Space is currently estimated as 64 MiB plus three times selected bundle bytes. This is an
explicit conservative starting estimate, not a measured bound for arbitrary repository expansion
or the planned 30 GB acceptance. Capacity management remains a later roadmap requirement.

## Interfaces

All plan HTTP routes require an authenticated administrator. Mutations also require the existing
CSRF header. Requests are bounded UTF-8 JSON, reject duplicate keys, and accept no execution
callback, URL override, embedded credential or success flag.

- `POST /api/v1/plans/prepare`: validate the explicit local selection and persist a draft.
- `POST /api/v1/plans/{id}/preview`: queue the read-only prerequisite preview for the reviewed
  digest.
- `GET /api/v1/plans` and `GET /api/v1/plans/{id}`: shared plan, operation and event state.
- `POST /api/v1/plans/{id}/queue`: body `{"digest":"<reviewed digest>"}` or, for the UI gate,
  `{"digest":"<reviewed digest>","preview_generation":N}`; requires a live worker. The
  generation is optional for legacy callers, but omitting it does not bypass execution preflight.
- `POST /api/v1/plans/{id}/cancel`: request cancellation at the next safe operation boundary.

Equivalent local operator commands are `gitferry plans prepare --input selection.json`,
`list`, `show --plan-id ID`, `preview --plan-id ID --digest DIGEST`,
`queue --plan-id ID --digest DIGEST --preview-generation N`, `cancel --plan-id ID`, and
`run --plan-id ID`. Each command accepts `--database PATH`; `GITFERRY_STATE_DB` supplies the
default. `run` without an ID selects the next queued or interrupted plan. Package and work roots
use `GITFERRY_PACKAGE_DIR` and `GITFERRY_WORK_DIR`; credentials and CAs use the existing local
reference directories. Execution requires these directories to exist.

## Recovery

A confirmed plan is a no-op on replay. A new plan ID cannot bypass unknown or conflicting
journal work for the same origin and target. If Gitea may have accepted a write before local
confirmation, recovery leaves it `outcome_unknown`; it does not repeat the POST. A dependent
comment that was never sent remains `blocked_parent`. These statuses are not successful imports.
Use the existing explicit reconciliation mechanism; comment resolution verifies the expected
repository, parent, native author ID/login and body. Requeueing does not resolve uncertainty.
Cancellation does not roll back confirmed writes. No API or CLI plan command can mark unexecuted
operations successful.

The preview, execution preflight, and subsequent Gitea writes do not form a distributed
transaction. Other writers can still change remote data between checks, including the documented
comment-read/PATCH gap. There is no general exactly-once guarantee, timestamp restoration, or
atomic PATCH contract. Replay is safe for confirmed reads and writes; an unknown write remains
blocked until explicit reconciliation.
