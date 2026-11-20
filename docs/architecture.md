# Architecture

## Capability map

| Module id | Responsibility | Depends on |
|---|---|---|
| `blueprint-management` | Versioned instance/transfer configuration, validation, secret references | — |
| `gitea-adapter` | Documented REST and Git-protocol boundary | `blueprint-management` |
| `identity-mapping` | Explicit realm/SID/OU identity resolution, separate native-author bindings and permission assignments | `blueprint-management` |
| `package-management` | Versioned portable manifests, bounded local files and hashes; signatures and archive extraction remain future work | `blueprint-management` |
| `job-management` | Durable bounded operations, mappings, baselines, conflicts, and local executor ownership | `blueprint-management` |
| `validation-audit` | Preflight, collisions, drift, reports, capability enforcement | all domain modules |
| `transfer-engine` | Orchestrates export/import/roundtrip without owning adapters | all domain modules |
| `rest-api` | Thin FastAPI application boundary | domain modules |
| `web-ui` | Guided Vue administrator workflow | `rest-api` |

Build order: blueprint management → adapters/mapping/package/operations → validation/audit → transfer engine → API → UI. The current implementation includes the thin blueprint/API slice, isolated feasibility probes, and the bounded durable-operation slice; it is not a transfer engine.

## Runtime boundaries

```text
CLI / Web UI
     |
REST API (input/auth boundary)
     |
Shared application services (worker now; CLI and UI callers later)
     |
+----------------------+-----------------+-------------+
| Shared application service (durable provisioning)    |
+----------+-----------+-------------+----------------+
| Gitea adapter | blueprints | packages | identity map |
+----------+-----------+-------------+----------------+
     |                         |
Gitea REST + Git       SQLite state + local payload volume
```

Jobs are server-side records and do not depend on a browser session. The shared application service
is the reusable execution boundary used by the worker and intended for later CLI and UI callers.
SQLite stores control state;
large Git/package bytes use bounded streaming on a persistent volume. No Docker socket is mounted
into the application.

The packaged OCI runtime initializes its local storage before readiness. One persistent root keeps
the SQLite database and its WAL/SHM files together with package and work directories. Unsafe paths,
permissions, corrupt databases, and unknown newer schemas stop startup rather than creating empty
state. See [ADR 0009](decisions/0009-oci-runtime-storage-and-readiness.md).

The same image contains the compiled Vue administrator client and serves it from the FastAPI
origin. A host operator creates the first local GitFerry administrator through the CLI; no default
credential exists. Password verifiers, session-verifier hashes, expiry, revocation, rate limiting
and security events share the persistent SQLite store. Raw passwords, session tokens and CSRF
tokens are not stored there. This local GitFerry identity is separate from technical Gitea
credentials and from functional source/target directory identities. See
[ADR 0010](decisions/0010-local-administrator-authentication.md).

## Core state model

Each bounded transfer round records source identity, target identity, selection, comparison-rule version,
baseline, desired state, last observation, operation intent hash, and object ID mappings. Durable
provisioning prepares intent before remote I/O, commits no SQLite transaction across the remote call,
and atomically commits mapping plus confirmation afterward. A target object is one of: absent,
explicitly mapped, foreign, or unknown after an interrupted write. Only absent and explicitly mapped
states can proceed automatically. A collision fails closed; an unknown parent blocks dependents.
Roundtrip compares selected-object baselines before applying any change; unrelated projects are
outside the selection and cannot be addressed.

An external directory identity, a local Gitea account, a historical native author, and a current permission subject are distinct records. Login or email equality never connects them implicitly. Issue mappings distinguish the global object ID from the repository issue index; comment mappings retain their global ID and mapped issue relation.

SQLite is local execution state. It is not an offline transport artifact and is never assumed to be
shared by exporter and importer. The UTF-8 JSON transport manifests carry stable origins,
instance-scoped replica facts, explicit comparison facts, and unapproved identity claims between
isolated stores. V1 remains the immutable issue-only contract. V2 adds normal issue comments with
typed parent-issue origins and qualified authors; portable `issue_comment` normalizes to the same
internal comment origin namespace. Local operation status, leases, file locks, credentials,
approvals, and conflict resolutions never cross that boundary. Neither version is a complete
package or transfer engine.

Durable provisioning uses schema v4 for structure origins/replicas and journal operation types.
Remote writes occur outside SQLite transactions. Exact team-unit verification is required for the
bounded proof; the live permission claim is limited to the explicitly configured `repo.code` and
`repo.issues` units. Credentials remain outside SQLite, plans, reports, and transport files. If a
remote outcome is uncertain, the operation remains `outcome_unknown` and dependent operations are
halted until explicit evidence-based reconciliation.

## Security and trust boundaries

Untrusted inputs cross at REST, YAML/JSON import, archive/package import, Git subprocess output, Gitea responses, and filesystem job payloads.

- Spoofing: authenticated administrator plus explicit Gitea credential references.
- Tampering: SHA-256 over exact stored manifest bytes now; asymmetric signatures before production
  packages. The current digest proves consistency, not authenticated origin.
- Repudiation: immutable job events and redacted reports are planned.
- Information disclosure: allowlisted exported fields and secret-reference-only schemas.
- Denial of service: input size/file-count/decompression limits and disk preflight.
- Elevation of privilege: no implicit identity match grants access; permissions require explicit validated mapping.

Subprocess arguments are passed as arrays without a shell. Extraction targets must resolve beneath an owned per-job directory. Gitea databases are never edited directly.

## API boundary

`/healthz`, `/readyz`, and the minimal bootstrap-status read remain public so an operator and the
container platform can establish service state. Login issues an opaque same-origin session;
sensitive API writes require an authenticated local administrator and a session-bound CSRF value.
Authentication failures do not disclose whether an account exists. The default cookie contract
requires HTTPS and is relaxed only by explicit localhost-only test construction.

The API currently exposes the protected blueprint-validation slice rather than a general transfer
control plane. The CLI additionally exposes
`transport create`, `transport verify`, and `transport admit`, all backed by the same
version-selected parser, validator, state store, and operation services used by the integration
worker. `state resolve-unknown-comment` performs live target/parent/body/author verification before
explicit reconciliation; its password comes from an environment-selected secret and is never
stored in SQLite. FastAPI's generated OpenAPI describes the GitFerry API, while each Gitea adapter
records the target instance version and OpenAPI digest.
