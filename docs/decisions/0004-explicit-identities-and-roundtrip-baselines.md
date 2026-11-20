# ADR-0004: Separate identity resolution, native authors, and permissions

## Status
Accepted

## Date
2026-11-20

## Context
Matching a username across directory realms can grant the wrong user access. Gitea local IDs also
differ between instances, while historical author restoration and current authorization have
different security effects. Roundtrip changes additionally need object-specific drift detection.

## Decision
Resolve an external identity only through an exact explicit key containing realm, login, external
ID, and OU path. Map that key to one existing non-admin local Gitea account. Historical author
bindings and repository permission assignments are separate records and separate operations.

Persist source/destination object mappings using repository plus object-specific identifiers.
Roundtrip baselines cover explicit allowlisted fields for selected objects; unrelated repositories
and global instance timestamps are not conflict signals.

## Alternatives considered
- Login-only mapping: rejected because equal names across realms are ambiguous.
- Email mapping: rejected because email can be missing or mutable.
- Numeric Gitea IDs as identity: rejected because IDs are instance-local.
- Migration-service author fallback: rejected because the executed Gitea migration attributed
  imported issues/comments to the technical administrator.
- Global instance timestamp: rejected because unrelated changes would block selected transfers.

## Consequences
Missing, duplicate, administrative, or already-bound targets fail closed. Mapping does not create
accounts or grant access. Native author restoration requires an approved mechanism to act as an
already mapped target account. Durable mapping/baseline storage is the next implementation slice;
the current report-backed probe is evidence, not a resume or synchronization engine.
