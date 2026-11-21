# ADR 0011: Local connection profiles and revision-bound trust

Status: accepted for Issue #8; execution integration belongs to Issue #9.

## Decision

Reuse `instances` as the durable identity namespace. SQLite migration 6 adds connection profiles,
revisioned observations and approval evidence, and an append-only profile event history. Existing
origins, replicas, identities and operations continue referencing the original instance key.
An active normalized endpoint has one profile. Display names and credential/CA references can be
edited with an expected revision. Endpoint and installation marker are immutable. Explicit
replacement retires the old profile and registers a new instance key atomically; mappings are
never inherited. Retirement retains historical registrations.

All connection URLs require HTTPS. Local token and PEM CA files are resolved from operator-owned
directories using simple aliases, bounded reads, regular-file checks and no symlink following.
Token files cannot be readable by group/others. No values are stored in profiles or exports.
HTTP redirects, proxy/environment credential discovery and verification-disable fallbacks are
disabled. Connection checks call Gitea `GET /api/v1/version` and `GET /api/v1/user` only.

The observed account, CA-content digest and TLS peer-certificate digest form connection evidence.
Only an explicit local approval of the current revision/evidence can approve a profile. Changed
evidence revokes approval; a failed check invalidates the current observation while retaining its
previous approval baseline. Imported configuration cannot carry approval or execution status.
The future executor must revalidate connection evidence before writes; this feature does not yet
wire existing low-level probe clients into that executor.

## Portability and compatibility

`schemas/v1/connection-profiles.schema.json` defines the independent UTF-8 JSON configuration
format. It is not a transport manifest or an identity-binding grant. Imports are atomic, bounded
to 100 profiles/256 KiB at API/CLI boundaries, reject duplicate JSON keys and unknown fields, and
cannot overwrite an existing profile. A profile may attach to an existing registered instance only
when its exact endpoint and installation marker match. Export omits retired profiles and all local
revision/approval state. Import requires resolving local files and testing/approving locally.

No new dependency was needed: the existing SQLite, HTTPX and JSON Schema stack suffices. Manifest
v1/v2 bytes and schema versions remain unchanged. SQLite and application/configuration versions
are independent.

## Limits and consequences

- Gitea does not provide a proven installation identifier here. An operator marker remains
  necessary. A rebuilt server reusing the same certificate and account identity is not detectable
  from these two read endpoints; the operator must use explicit installation replacement.
- Certificate renewal changes the evidence and requires review. Trusting a CA does not silently
  accept an unreviewed replacement certificate for an already approved profile.
- Read checks establish reachability and authenticated identity, not complete transfer permissions.
- Verification and a later write are not one atomic remote transaction. No concurrent foreign-write
  or exactly-once guarantee is introduced.
- Existing timestamp, comment-PATCH and unqualified permission limitations remain unchanged.

## Evidence

`test_connection_profiles.py`, `test_connection_probe.py`, and `test_profile_api.py` cover state,
CLI/API, concurrency, rollback, restart, untrusted configuration and local approval. Browser tests
exercise the actual FastAPI/SQLite application. `connection_profile_probe.py` checks real Gitea
1.27.2 through a generated local HTTPS termination point, including CA, hostname and expiry
failures. Reported evidence must distinguish that TLS termination from native Gitea TLS deployment.
