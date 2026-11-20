# ADR-0005: SQLite journal and restart boundary

## Status
Accepted

## Date
2026-11-20

## Context
The bounded issue probes already use explicit identity and object mappings and selected-field
baselines. Process-local dictionaries cannot distinguish a confirmed prior write from an
interrupted write whose Gitea outcome is unknown. SQLite and Gitea cannot participate in one
atomic transaction, so a crash can occur after Gitea accepts a request and before GitFerry records
the target identifier.

The state contains control metadata, not large Git/package payloads. One active writer is enough
for this slice. Exporter and importer may run on different hosts and therefore cannot be assumed to
share a database.

## Decision
Use Python's standard-library `sqlite3` driver and a configurable file path; add no dependency.
Every connection enables and verifies `foreign_keys`, selects WAL mode, sets
`synchronous=FULL`, and uses a bounded busy timeout. The database, `-wal`, `-shm`, and executor lock
must live together on one persistent local filesystem. WAL is not supported here on a network
filesystem.

Version the schema with `PRAGMA user_version` and ordered application migrations. Refuse unknown
newer versions and non-empty unversioned databases. Registrations, identity bindings, origins,
target mappings, baseline versions, operations, events, and conflicts are related with foreign
keys and protected by database uniqueness constraints.

An operator supplies an instance registration key and installation marker. A URL is descriptive,
not identity; a rebuilt Gitea at the same URL receives a new registration and marker. GitFerry does
not assume an automatic Gitea installation identifier.

For a write operation:

1. Persist immutable intent and its SHA-256 hash as `prepared`.
2. Acquire the local executor file lock and durable lease, then atomically claim `executing`.
3. Commit that claim and call Gitea without an open SQLite write transaction.
4. On a usable success response, insert the target mapping and mark `confirmed` in one SQLite
   transaction.
5. If a new process finds an abandoned `executing` operation, or a response is unusable after the
   request might have succeeded, record `outcome_unknown` and stop. Do not issue another POST.

The file lock is released by the operating system after a process death. A successor that obtains
that lock may convert operations owned by the abandoned durable lease to `outcome_unknown`. This
prevents CLI and backend writers sharing the same state volume from running concurrently; it is not
a distributed lock.

Explicit reconciliation requires a stable target object ID plus an exact match to the persisted
intent hash. Title, text, or an approximate timestamp without the stable ID is insufficient. The
resolution is append-audited and atomically creates the mapping. It is an operator-confirmed
recovery, not automatic discovery.

## Alternatives considered
- Process-local state: rejected because confirmed progress and ambiguity disappear on restart.
- A new ORM/migration dependency: rejected because the standard library supports the required
  bounded schema and transactions.
- One SQLite transaction around the Gitea call: rejected because it would hold a writer lock across
  network I/O and still could not make the remote write atomic.
- Automatic retry of interrupted POST: rejected because a local unique constraint cannot prevent a
  duplicate external Gitea object.
- Direct Gitea database updates: rejected by the project boundary and not needed.
- Shared SQLite database between offline exporter/importer: rejected because WAL requires a local
  shared host/filesystem model and the deployment boundary does not guarantee shared storage.

## Consequences
Confirmed operations can be replayed as local no-ops after a real process restart. Ambiguous
creates remain visibly halted until explicitly reconciled. The implementation provides no general
exactly-once guarantee across SQLite and Gitea. ADR-0006 now defines the portable manifest that
carries stable origin and transfer facts between isolated installations without copying operational
SQLite state wholesale.

The durability settings follow SQLite's documented requirements: foreign keys are enabled per
connection, WAL has `-wal`/`-shm` companion files, and `synchronous=FULL` adds a WAL sync for each
commit. See [SQLite foreign keys](https://www.sqlite.org/foreignkeys.html),
[WAL](https://www.sqlite.org/wal.html), and
[`synchronous`](https://www.sqlite.org/pragma.html#pragma_synchronous).
