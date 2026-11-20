# ADR-0006: Portable manifest origin, baselines, and trust boundary

## Status
Accepted

## Date
2026-11-20

## Context
Exporter and importer have independent local SQLite stores and may be unable to contact the other
Gitea. Copying SQLite would mix local executor state, leases, approvals, and conflicts with portable
domain facts. A return transfer must preserve an A-born origin through its B replica, recognize a
B-born object as a B origin, and compare both the original A baseline and the confirmed B import
state. Manifest identity, transfer-round identity, and local operation identity serve different
purposes and cannot be collapsed.

The transport input is untrusted. Equal titles, logins, URLs, or native numeric IDs do not prove
identity. A manifest must not authorize credentials, identities, administrative rights, writes to
another target, successful operations, or conflict resolutions.

## Decision
Define `gitferry.transport` schema version 1 as bounded UTF-8 JSON validated by the local Draft
2020-12 schema. The YAML blueprints remain separate configuration models. V1 is deliberately
limited to issues and the selected fields title, body, state, and label names.

The immutable file is `manifest.json`. GitFerry's writer emits one deterministic JSON encoding, but
receivers hash the exact stored bytes without parsing or normalization first. A separate
`manifest.json.sha256` contains the SHA-256; the manifest does not contain its own hash. Payload
entries, when present, name only safe relative paths and include exact byte size and SHA-256. No
remote schema or payload loading is permitted.

The header distinguishes immutable `manifest_id`, the overarching `transfer_round_id`, and local
operation IDs. A return references the exact forward manifest ID and exact byte digest. Same ID and
same digest is a replay; same ID and another digest is a conflict. A new envelope does not create a
new object origin and cannot bypass an existing confirmed or `outcome_unknown` operation.

Object origins use the registered origin instance, repository scope, object type, and native global
ID. Global IDs and issue indexes are transported as decimal strings. Replicas are bound to a
specific registered instance and repository. Instance references include the operator-issued
installation marker; URL is descriptive and does not identify an installation.

Each transported issue carries the original comparison facts, confirmed import facts where
available, current desired facts, and comparison-ruleset version. A hash alone is insufficient for
drift decisions. Return admission checks the original facts against the locally recorded forward
manifest and never replaces an existing A baseline. B freezes its selected import observation
before deriving later B changes.

Schema migration 2 records immutable exported/received manifests, their object facts, unapproved
identity claims, and confirmed import observations. Admission is one short SQLite transaction with
foreign keys and uniqueness constraints. Validation and admission perform no Gitea writes.
Operations, events, leases, file locks, credentials, identity approvals, and manual conflict
decisions stay local. Identity data in the manifest is an observed or proposed claim only; normal
local mapping policy remains authoritative.

## Alternatives considered
- Share or synchronize SQLite databases: rejected because execution state is local and WAL is not a
  portable package format.
- Match by title, login, URL, or coincident numeric ID: rejected because none proves origin.
- Put a manifest hash inside the same manifest: rejected because it is self-referential.
- Parse and reserialize before hashing: rejected because it would not verify the transferred bytes.
- Treat a received manifest as an applied job: rejected because validation, admission, and Gitea
  application are distinct states.
- Accept remote approvals or confirmed-operation markers: rejected because local authority cannot
  be delegated by untrusted transport data.

## Consequences
Issue facts can complete an offline-style A → B → A exchange through files while each side retains
its own state store and target authority. Replays and repackaging reuse stable origins and local
operation outcomes. Selected A drift still blocks application, while unselected data is untouched
and absence never means deletion.

SHA-256 provides integrity and consistency only; it does not authenticate the sender. Signatures,
encryption, a complete archive format, comments and other metadata types, general merge logic, and
a full transfer engine remain outside v1. Native historical creation times remain origin facts and
are not restored through the regular Gitea create endpoints.
