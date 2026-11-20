# ADR-0007: Manifest v2 comment parent, execution order, and compatibility

## Status
Accepted

## Date
2026-11-20

## Context
Normal issue comments have their own durable identity but cannot be applied without an unambiguous
parent issue. Native comment IDs, matching text, titles, URLs, or file order do not prove that
relationship. A comment create may also succeed remotely while GitFerry loses the result. A later
package or process must not bypass that uncertainty. Existing manifest-v1 files and their exact
byte digests are already portable evidence and cannot be rewritten to introduce comment support.

## Decision
Add a separate Draft 2020-12 schema for `gitferry.transport` version 2. Parser selection is based
on `schema_version`; the v1 schema, serialization, accepted files, origins, and replica bindings are
unchanged. Manifest version and SQLite schema version remain independent.

V2 adds `issue_comment` as the portable type. It is normalized to the existing internal `comment`
object type so upgrading a transport envelope does not create a second origin namespace. Every
comment origin contains `parent_issue_origin`, a typed reference to the stable issue origin, plus a
realm-qualified author selector. Comment replicas carry the native comment ID and native parent
issue index for their concrete instance. The comparison ruleset is
`gitea-issue-comment-body` version 1; body is the only selected mutable field. Original and native
creation times remain separate.

Admission validates the complete graph before recording facts. File order is not execution order:
an included issue origin is inserted and applied before dependent comments. A parent may instead
already have a non-conflicting local origin and target mapping. Missing or changed parents reject
admission or persist the dependent operation as `blocked_parent`. An unknown parent create never
permits a child POST. A comment's own interrupted write remains `outcome_unknown`; those states are
not interchangeable.

SQLite schema migration 3 extends the bounded journal with `update_comment` and
`blocked_parent`. Parent unblock requires the exact immutable parent origin and a confirmed mapping
on the same target instance. Stable comment operation IDs are derived from origin, operation type,
and complete non-secret intent, independent of manifest ID. Repackaging therefore cannot bypass a
confirmed, conflicted, or unknown operation.

Before PATCH, the adapter lists all comments of the expected issue without a time filter and
verifies the mapped ID, repository/instance chosen by local configuration, actual issue relation,
native mapped author, and expected body baseline. Missing, drifted, or differently parented targets
become conflicts; GitFerry neither patches nor recreates them. Explicit unknown-create resolution
uses the same complete parent listing and verifies body and mapped native author before recording a
mapping.

The live Gitea 1.27.2 OpenAPI schema `EditIssueCommentOption` contains only `body`; it exposes no
content version, ETag, or other atomic precondition. The read-before-PATCH check therefore narrows
but cannot eliminate a race with independent writers.

## Alternatives considered
- Encode the parent as native issue ID or URL: rejected because replicas differ by instance and
  URLs are not installation identity.
- Infer parent by file order: rejected because JSON array order must not carry domain semantics.
- Match unknown creates by text and approximate time: rejected because equal comments are valid
  distinct objects.
- Upgrade or rewrite v1 bytes in place: rejected because it invalidates established digests and
  immutable manifest identity.
- Use the technical importer when author mapping is absent: rejected because it falsifies the
  native functional author.
- Treat read-before-PATCH as compare-and-swap: rejected because the tested API has no atomic
  server-side precondition.

## Consequences
Normal issue-comment creation and body updates can participate in the separate-state file
roundtrip with stable origins and verified parents. Two equal comment bodies remain distinct.
Confirmed replays are no-ops; hard exits and lost responses halt without a second POST. V1 remains
usable and byte-stable.

The support claim excludes deletion, parent moves, PR review and inline comments, system timeline
events, reactions, attachments, embedded-link rewriting, general merges, signatures, encryption,
and general exactly-once behavior.
