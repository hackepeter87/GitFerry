# Manifest v2 issue-comment roundtrip verification

## Executed scope

On 2026-11-20, `make test-integration` completed from fresh volumes with two pinned Gitea 1.27.2
instances (`sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`).
GitFerry A and B used independent SQLite named volumes and disjoint networks. The harness copied
only `manifest.json`, its exact-byte SHA-256 sidecar, and declared transport files. No SQLite file,
Python object, credential file, or hidden mapping crossed sides.

Interfaces exercised:

- local manifest-v1 and manifest-v2 Draft 2020-12 schemas selected by version;
- SQLite schema migration 3, immutable origins/replicas/baselines, and the existing executor;
- Gitea REST issue GET/POST and normal issue-comment list/POST/PATCH endpoints;
- live `/swagger.v1.json` schema `EditIssueCommentOption`;
- functional accounts `issue-author`, `comment-author`, and `comment-author-b`, all distinct from
  `gitferry-importer` and from the technical administrator.

## Transported and local facts

V2 transported stable issue and comment origins, explicit `parent_issue_origin`, instance-scoped
replicas, qualified author selectors, original and confirmed-import body facts, current body,
comparison-ruleset version, origin timestamps, native replica timestamps, and the exact forward
manifest reference. Native IDs were decimal strings. Missing objects retained
`absence-does-not-delete` semantics.

Credentials, connection authorization, confirmed identity bindings, administrative grants,
operations, events, executor leases, file locks, conflict decisions, and approvals remained local.
Transported author relationships were data to verify; the receiving side used only its previously
confirmed exact realm/login/external-ID/OU binding.

## Executed roundtrip cases

Forward manifest `comments-v2-forward-1` had byte digest
`1abe44749360015166627253744fd4d6ecca9b054827ccab6dccdb15ddb57a77`. B created exactly one
issue copy and one ordinary comment copy. A second worker process replayed the manifest without a
second POST.

Return manifest `comments-v2-return-1` had byte digest
`5c1116e4be878e5a69b0e92a6702cf935228ceae3b00f074e29ae6794ec96d2c`.

- Case A passed: B changed Unicode/Markdown/multiline content of an A-origin comment. A updated the
  original native comment ID `5` on issue index `10`; no new A comment appeared. Native author
  `comment-author` and native `created_at` remained unchanged.
- Case B passed: B created a new B-origin comment on the A-origin issue. A created it once on the
  original A issue, with mapped native author `comment-author-b`. Its B origin remained B and its
  parent origin remained A.
- Case C passed: B created a B-origin issue and child comment. The return file intentionally listed
  the comment before the issue. A first confirmed the issue mapping, then created the comment on
  that exact target issue. Both retained B origins.

Same-return replay after a real process restart created no objects. Repackaged manifest
`comments-v2-return-repackaged`, digest
`ad04a7d79f3c39e28cb845178ce0f5c2b1c276dbd62090432f1dc9b7c80ac1d5`, retained the same origins
and reused confirmed local operations without duplicates.

## Conflict and restart evidence

- Relevant A comment-body drift blocked before any return create or update. A change in the
  unselected control repository neither blocked the return nor got overwritten.
- A hard process exit after Gitea accepted a comment POST left the operation `executing`; the next
  process converted it to `outcome_unknown`. GitFerry had no target ID, and retry produced no POST.
- A deliberately discarded successful HTTP response produced the same durable halt and no second
  POST after restart.
- Unit tests additionally covered equal-body comments with distinct origins, missing exact author
  mapping, missing/changed parents, parent-blocked operation without child POST, stale body,
  comment ID belonging to another issue, explicit unknown resolution against the expected issue,
  disappearing target, changed operation intent, and v1 byte/digest stability.
- 82 backend tests passed. The complete integration report records every comment assertion as
  `true` under `comment_transport_roundtrip.assertions`.

## API and product boundaries

The live Gitea 1.27.2 `EditIssueCommentOption` request exposes only `body`; there is no tested
atomic conditional-update field. A foreign writer can therefore race the membership/baseline read
and PATCH. GitFerry does not claim compare-and-swap safety for that gap.

Supported here: normal issue-comment creation, body update, stable origin, verified parent,
qualified mapped author, selected-body drift, confirmed replay, parent block, and unknown-outcome
halt/reconciliation.

Not supported here: deletion, parent moves, PR review/inline comments, system timeline events,
reactions, attachments, reference rewriting, general merge, signatures, encryption, or a general
exactly-once guarantee. Original timestamps are retained as origin facts; new native timestamps are
separate and are not historical restoration.
