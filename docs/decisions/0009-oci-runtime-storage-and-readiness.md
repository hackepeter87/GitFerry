# ADR 0009: OCI runtime storage and readiness boundary

Status: accepted for Issue #6

Date: 2026-11-20

## Context

The development image proved that GitFerry could be built, but it did not define an operator-safe
container lifecycle. SQLite control state, package files, and work files must survive container
replacement. Unsafe storage must fail closed before the service reports readiness. The runtime
must not require a Docker socket, privileged mode, a browser session, or an external database.

## Decision

The packaged application uses one operator-owned persistent root with three distinct absolute
paths: the SQLite database, package directory, and work directory. Startup rejects relative or
overlapping paths, symbolic-link components, broad permissions, non-regular database files, and
unwritable directories. Newly created directories use mode `0700`; the database uses `0600`.

FastAPI lifespan initialization performs this preflight and applies the ordered SQLite migrations.
`/readyz` is unsuccessful before initialization and becomes healthy only after the supported schema
is open with foreign keys, WAL, and full synchronization enabled. `/healthz` remains a narrow
process-liveness signal. Corrupt or unknown-newer schemas terminate startup without replacing the
existing file.

The image runs as UID/GID `10001`, declares SIGTERM as its stop signal, includes a local readiness
healthcheck, and records application version and source revision in OCI labels. The reference
Compose file removes capabilities, enables `no-new-privileges`, uses a read-only root filesystem,
and mounts credentials as secret files and internal CAs read-only. It neither mounts the Docker
socket nor grants privileged mode.

SQLite, WAL/SHM companion files, packages, and work files remain on the persistent volume. Live
filesystem copies of only the primary database are not a supported backup. The current single
active writer rule remains unchanged.

## Consequences

Confirmed mappings, baselines, operations, conflicts, and unresolved `outcome_unknown` records can
survive graceful replacement and forced process restart. Readiness has a precise storage/schema
meaning instead of merely proving that a Python process started.

This decision does not implement administrative authentication, connection profiles, a general
background job service, multi-host writers, automated backup/restore, Kubernetes packaging, or a
stable published image. Durable execution visible through the UI remains owned by later 0.2.0
issues; backup/restore qualification remains owned by 0.6.0.

## Alternatives considered

- Store SQLite in the container writable layer: rejected because container replacement would lose
  confirmed progress and conflicts.
- Mark readiness healthy before migration: rejected because callers could address an incompatible
  or unusable store.
- Repair or replace an unknown database automatically: rejected because it could silently discard
  state.
- Mount the Docker socket for lifecycle control: rejected because GitFerry does not need host
  container authority.
- Put only the main SQLite file on persistent storage: rejected because WAL/SHM and package/work
  state are part of the operational boundary.

## Evidence

The reusable checks are in `backend/tests/test_runtime.py`,
`backend/tests/test_container_contract.py`, and `tests/integration/oci_runtime_probe.py`. The
integration probe validates the built image, replaces the container while retaining its volume,
forces a process kill, and checks corrupt/newer-schema/read-only/symlink startup failures.
