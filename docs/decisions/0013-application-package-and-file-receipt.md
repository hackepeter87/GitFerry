# ADR 0013: Application packages bind selections to unchanged metadata manifests

Status: proposed under [Issue #10](https://github.com/hackepeter87/GitFerry/issues/10).

## Context

Metadata manifests v1/v2 already preserve origins, replica mappings, selected comparison facts,
and v2 comment parents/authors. They do not describe a complete first-import selection: issue
records have no author selector, and organization/team selections are separate from Git payloads.
A Git-only provisioning selection must not fabricate an issue to satisfy a metadata schema.
The application needs a portable file boundary before local planning can connect these parts.

## Decision

Add `gitferry.application-package`, schema version 1, independently of the metadata manifest,
SQLite, and application versions. The descriptor binds repository/organization/team origins,
team membership **claims**, qualified issue-author selectors, declared realms, and Git bundle
paths/sizes/digests. Optional `metadata/manifest.json` remains an unchanged v1 or v2 artifact,
with its original sidecar. The package references its exact ID and byte digest and requires
matching sender, recipient, forward mode and transfer round. Included issue origins have
exactly one explicit author relation; v2 comment authors remain in their existing manifest.
A realm description is data, not a working LDAP/AD integration or permission to create a realm.
Every embedded object origin must name a selected repository scope; merely listing the right
repository in the manifest selection is insufficient. Cross-scope repository renaming is not
implemented in this first-import package slice and never invents a replacement origin.

`package.json` uses UTF-8, sorted keys, compact JSON separators and one trailing newline when
written by GitFerry. `package.sha256` contains lowercase SHA-256 of those exact stored bytes
and a trailing newline. This serialization is a project writing convention, not a claim of
standard JSON canonicalization. Readers hash the received bytes without normalization.
All files are declared and checked; unrelated files are rejected rather than accidentally
exported. Native object IDs are strings. Absence never means deletion. No local profile,
secret reference, approved binding, operation status, lease or conflict decision is portable.

A streaming, uncompressed ZIP64 carrier provides one downloadable/uploadable file. Individual
files retain their own digest checks; this does not replace the manifest/payload contract in
ADR 0002. Reception uses a private staging directory, bounded entry/byte limits, canonical
relative file names, no links or special files, and validation before publication. Unsupported
compression and path aliases are rejected. No whole payload is buffered in memory. The ZIP central directory is bounded before Python
constructs its index (at most 1,024 bytes per allowed entry). This uses CPython’s bounded EOCD
reader, covered by compatibility tests; a missing helper fails closed. File and directory data
are flushed before successful publication. Directory publication uses Linux `renameat2` with
`RENAME_NOREPLACE` or Darwin `renamex_np` with `RENAME_EXCL`; unsupported platforms fail
closed rather than silently replacing an existing destination.

Inspection and receipt perform no network access or SQLite writes. The result is
`validated-files-only`, never a migrated or approved plan. Destination registrations, binding
approval, target credentials and remote execution remain local and continue through the existing
application core. New package IDs cannot legitimately change an object's origin or clear a
journal conflict; package admission/planning must preserve those existing checks.

## Scope and consequences

This slice implements the portable file boundary and CLI, not the completed first-import wizard
or transactional admission of structure facts. `inventory_complete` is fixed to false until a
supported content inventory supplies stronger evidence. Receipt must not be presented as full
migration. The integration probe receives the carrier in its actual target-only container and
uses the validated bundle in the existing durable plan. Its synthetic structure/author setup
remains the existing independent plan fixture; this is not yet the complete guided source export.

SHA-256 detects inconsistency, not authenticated origin. Signing and trust management remain
0.6 work. Historical native timestamp limits and the comment read/PATCH race are unchanged.
No schema migration or new dependency is needed for this file-only step. Durable receipt
registration and UI execution must not be simulated by adding success fields to a package.
