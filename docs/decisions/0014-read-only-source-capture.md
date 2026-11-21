# ADR 0014: Durable source capture before portable first-import planning

Status: accepted in PR #53 under [Issue #10](https://github.com/hackepeter87/GitFerry/issues/10).

## Context

The application package binds structure, Git payloads and unchanged metadata manifests, but
the first-import workflow needs to produce those files from a locally approved source. The
recipient can be offline. Capturing data must not grant identity bindings, contact the recipient,
or pretend that an incomplete inventory is a complete migration.

## Decision

`SourceExports` records an immutable selected request, profile revision and approved connection
identity before reading. It uses the existing TLS/secret resolver and connection probe, the
existing Git bundle component, and existing origin, replica, baseline and transport rules.
SQLite migration 8 adds the local `source_exports` table. Manifest v1/v2, application-package v1,
and their byte digest contracts are unchanged. No dependency is added.

Only selected organization repositories are captured. API access reads repository identity,
organization, selected teams/members, issues and normal comments. Git uses authenticated HTTPS
reads; smart Git can use POST for `git-upload-pack`, which is not an API mutation. The API and
Git use the captured credential and CA trust anchors; Git does not reopen a mutable local CA
reference. A context without usable CA anchors fails closed. The recipient needs a stable local
registration but no connection profile, credential or network reachability.

Metadata and structure are read again after bundle creation, and all selected refs are checked
again. A difference blocks capture. This is an observed stability check, not an atomic snapshot
or a lock against concurrent Gitea writers. Changes that happen and revert between observations
are not detectable. Unselected repositories do not participate in this comparison.

Existing replica mappings recover permanent origins. A source copy never acquires a new origin
merely because it lives on another instance. Stale scope, parent, identity or origin conflicts
remain errors. Captured source observations become new, explicitly identified transfer baselines;
prior rounds are not replaced. The existing transport admission helpers execute in the same
SQLite transaction as origins, source replicas, baselines and local completion.

Native Gitea accounts are exported as observed, unapproved claims in a `gitea-local` realm derived
from the registered instance key and installation marker. Their external identifier is the
native account ID. The mandatory existing selector `ou_path` uses `/` for this local namespace
root; it does not assert an LDAP OU. No directory identity, target account, permission grant or
approved binding is inferred. Issue and comment authors remain independent qualified claims.

## Local completion and restart

The local states are `prepared → capturing → captured`, with `failed` for a rejected capture.
The shared executor lock serializes capture with application writes. Network reads occur outside
SQLite write transactions. A capture has no remote mutation and therefore does not create an
`outcome_unknown` remote operation or an externally confirmed migration.

After validation, the package's exact byte digest is persisted as a local candidate. Files and
directories are flushed and published without replacement. Finally, all captured facts and
`captured` status commit atomically. A crash after publication can recover from the files only
when they match the saved candidate digest and original local intent. Recovery does not read
the source again. Confirmed replay verifies the saved package digest. A changed request cannot
reuse the export ID, and an arbitrary preexisting package cannot substitute for a capture.
Partial private staging is removed on retry; cleanup is restricted to that export's work path.

## Scope and consequences

The service and operator CLI are building blocks for the guided workflow. A completed export
does not mean target admission or migration succeeded. The protected web flow, portable receipt
planning and explicit local author/right decisions remain Issue #10 acceptance work.

The first bounded capture supports ordinary branches/tags, organizations, representable read/write
teams, optional member claims, issues and normal comments. Split or administrative team rights
are reported as omitted. Additional permission units are not claimed. LFS, wiki, attachments,
release files, PRs/reviews and other content require their existing roadmap work. Inventory
completeness stays false. Pagination, response bytes, nesting and aggregate metadata are bounded;
exceeding a bound fails instead of silently truncating. Large-selection qualification remains open.

Native source `created_at` and historical origin times remain separate facts. `updated_at`
participates in capture drift checks but is not added as a restorable field to existing manifests.
Historical target timestamps, atomic comment PATCH preconditions, signatures and a general
exactly-once guarantee remain unsupported. No direct Gitea database access is introduced.

## Verification

`test_source_exports.py` covers stable origins/parents, no automatic identity approval, immutable
intent, relevant drift, changed local approval, atomic rollback, candidate digest checks and a
real child-process interruption/restart. `test_source_snapshot.py` covers selected API reads,
credential reflection and pagination failures. `test_gitea_bundles.py` checks mirror/ref capture.
These unit tests alone do not establish the complete first-import workflow; real-instance and
hosted evidence is recorded separately in the testing documentation.

### Storage qualification boundary

Current explicit API/metadata bounds do not bound bytes received by `git clone --mirror`.
The export setting `GITFERRY_EXPORT_MAX_GIT_BYTES` (default 1 GiB) checks the completed clone
before bundle creation and the combined output bundle bytes. Clone staging is removed after
bundle creation. Bundle generation and verification have 60-second subprocess timeouts.
These guards reduce amplification but do not enforce a strict aggregate filesystem quota.
Operators need a capacity-controlled runtime volume; enforceable temporary-storage isolation,
large selections and the practical 30 GB proof remain 0.6 acceptance work. Directory polling,
per-file resource limits or a post-write hash must not be described as a portable hard quota.

Application-package v1 has no separate historical structure-scope field. A known structure copy
with a renamed organization/repository scope is therefore rejected explicitly, retaining its
existing origin and mapping. It never creates a replacement origin in the current scope.
