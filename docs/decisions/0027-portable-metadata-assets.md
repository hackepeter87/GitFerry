# ADR 0027: Portable labels, attachments and releases

Status: accepted

## Context

Issues and ordinary comments do not cover repository labels, issue/comment attachments,
releases or release assets. These objects have separate Gitea identifiers, parent
relationships and binary payload endpoints. A title, filename or numeric ID is not a
portable identity, and package paths must not become a file-system or network escape.

## Decision

- Application-package v4 represents labels, issue/comment attachments, releases and
  release assets as typed records with stable origin keys and instance-scoped replicas.
- Attachment and release-asset records carry an explicit parent origin, a relative payload
  reference, byte size and SHA-256 digest. Admission rejects absolute, traversal, symlink,
  external-URL, inaccessible, duplicate or over-limit payload references before target writes.
- Label, release and binary writes use the existing operation journal and executor lease. A
  confirmed operation is replay-safe. A remote write whose result is lost is persisted as
  `outcome_unknown` and is never retried as an automatic second POST; explicit reconciliation
  must verify target instance, repository, parent, content and mapped author where applicable.
- Author-bearing release records require an explicit local identity binding. The technical
  importer is not used as a fallback author. Attachment authors and historical native
  timestamps are not reconstructed where the tested Gitea endpoint does not accept them;
  origin values remain separate provenance facts.
- Package omission is not deletion. Labels, releases, assets and relations not present in a
  selected package remain untouched. Collision, update and drift decisions are local and do
  not follow from equal names or IDs.

## Evidence and scope

The reproducible integration baseline is Gitea 28.4.0. The application-plan probe creates a
mapped label and assignment, an explicitly authored release, issue/release binary payloads,
and verifies parent relationships, bytes, replay and interrupted-operation handling against
real Gitea endpoints. Unit and package tests cover digest/path/size validation, origin and
parent conflicts, journal transitions and secret-safe diagnostics. Transport Manifest v1 and
v2 remain readable and retain their original bytes and identities; application-package v4 is
selected explicitly by its independent package schema and validator.

This decision does not claim label/release deletion, general merge semantics, arbitrary URL
fetching, historical native timestamp restoration, attachment author fidelity, or a complete
return workflow. Those limits remain visible in the capability matrix and issue #19.

## Consequences

Portable metadata can be transferred without sharing SQLite state or trusting package claims
to authorize local accounts. The application must retain payload files until validation and
execution complete, and a failed or unknown binary write remains visible for reconciliation.
Adding another metadata class requires its own API contract, parent/origin model, payload
rules and real Gitea evidence rather than inheriting issue or Git claims.
