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
Application services (shared by CLI and UI)
     |
+----+----------+-----------+-------------+
| Gitea adapter | blueprints| packages    | identity mapping
+----+----------+-----------+-------------+
     |                     |
Gitea REST + Git        SQLite job/audit state + local payload volume
```

Jobs are server-side records and do not depend on a browser session. SQLite stores control state; large Git/package bytes use bounded streaming on a persistent volume. No Docker socket is mounted into the application.

## Core state model

Each bounded transfer round records source identity, target identity, selection, comparison-rule version, baseline, desired state, last observation, operation intent hash, and object ID mappings. A target object is one of: absent, explicitly mapped, foreign, or unknown after an interrupted write. Only absent and explicitly mapped states can proceed automatically. Roundtrip compares selected-object baselines before applying any change; unrelated projects are outside the selection and cannot be addressed.

An external directory identity, a local Gitea account, a historical native author, and a current permission subject are distinct records. Login or email equality never connects them implicitly. Issue mappings distinguish the global object ID from the repository issue index; comment mappings retain their global ID and mapped issue relation.

SQLite is local execution state. It is not an offline transport artifact and is never assumed to be
shared by exporter and importer. The UTF-8 JSON transport manifests carry stable origins,
instance-scoped replica facts, explicit comparison facts, and unapproved identity claims between
isolated stores. V1 remains the immutable issue-only contract. V2 adds normal issue comments with
typed parent-issue origins and qualified authors; portable `issue_comment` normalizes to the same
internal comment origin namespace. Local operation status, leases, file locks, credentials,
approvals, and conflict resolutions never cross that boundary. Neither version is a complete
package or transfer engine.

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

Iteration 1 exposes only health and blueprint validation over HTTP. The CLI additionally exposes
`transport create`, `transport verify`, and `transport admit`, all backed by the same
version-selected parser, validator, state store, and operation services used by the integration
worker. `state resolve-unknown-comment` performs live target/parent/body/author verification before
explicit reconciliation; its password comes from an environment-selected secret and is never
stored in SQLite. FastAPI's generated OpenAPI describes the GitFerry API, while each Gitea adapter
records the target instance version and OpenAPI digest.
