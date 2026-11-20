# Durable state and controlled restart verification

## Scope

This slice persists the already bounded identity, object-mapping, baseline, and issue-write
contracts. It is not a transfer engine, distributed job platform, automatic reconciliation engine,
or general exactly-once implementation.

Test target: two isolated Gitea 1.27.2 instances from the pinned image digest
`sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`.
Interfaces exercised: regular issue `POST`, issue `GET`/`PATCH`, and the existing Git/organization
probes. All identities and content are synthetic.

## State model

The version-1 SQLite schema stores:

- operator-registered Gitea instances, with registration key, URL, and installation marker;
- directory realms independently from Gitea instances;
- immutable confirmed identity bindings, including realm/login/external ID/OU and target instance,
  account ID, and login;
- canonical object origins and target-instance-specific mappings;
- transfer rounds and baselines with immutable source baseline, desired state, and revisioned last
  observation in separate columns;
- operations, append-only transition events, conflicts, and the one-writer lease.

No API token, password, or LDAP password is stored. Intent validation rejects secret-like fields;
secret references remain separate configuration data.

### Instance and object identity

A URL does not identify an installation. Registration requires an operator-defined registration
key and installation marker. Rebuilding a Gitea instance at the same URL requires a new key and
marker. No undocumented Gitea installation identifier is assumed.

An origin is keyed by origin instance, repository scope, object type, and original global object
ID. Issue index and comment relation are separate fields. A mapping additionally names its target
instance, so one origin can map independently to several targets. Transfers add mappings to the
same origin record; they do not replace A origin with B merely because an A object currently exists
on B. An object first created on B receives its own B origin.

## Transactions and status transitions

```text
prepared -> executing -> confirmed
                 |
                 +-> outcome_unknown -> confirmed (explicit reconciliation only)
                 |
                 +-> failed/conflict (recorded terminal evidence)
```

Intent is committed before the HTTP call. The `executing` claim is committed before Gitea is
called. No SQLite write transaction remains open during network I/O. A usable result commits target
mapping, target identifier, and `confirmed` together. The injected local-commit fault proved that
the mapping and completion status roll back together.

The database uses WAL, `synchronous=FULL`, a five-second busy timeout, and verified
`foreign_keys=ON` on every connection. SQLite documents that foreign keys are connection-local,
that WAL maintains `-wal` and `-shm` companions, and that FULL adds an fsync at each WAL commit.
All companion files and the `.executor.lock` must be placed on the same persistent local volume.
Do not place this database on a network filesystem.

The schema is migrated in ordered versions and recorded in both `PRAGMA user_version` and
`schema_migrations`. A database with a newer version or unrecognized existing tables is rejected;
it is not renamed, reset, or treated as empty.

## Controlled recovery

The local executor lock and durable lease allow one writer for a state volume. After a clean exit,
the lease is removed. After process death, the operating system releases the file lock while the
durable lease and `executing` row remain. The next lock owner records those rows as
`outcome_unknown` before considering any write.

`gitferry state operation` displays status, hash, target identifiers, transition events, conflicts,
and available evidence. An explicit resolution is available:

```bash
GITFERRY_STATE_DB=/var/lib/gitferry/state/gitferry.sqlite3 \
  gitferry state operation --operation-id op-17

GITFERRY_STATE_DB=/var/lib/gitferry/state/gitferry.sqlite3 \
  gitferry state resolve-unknown \
  --operation-id op-17 \
  --target-global-id 71 \
  --target-issue-index 8 \
  --observed-intent verified-target-71.json \
  --evidence "Fetched stable target id 71 from the registered target/repository"
```

Resolution requires the stable target ID and an exact canonical-content hash match. The JSON file
must be independently fetched for that ID from the registered target and must not contain secrets.
The command does not infer identity from similar title/body/time values and does not itself discover
an object. Without exact evidence, the operation stays halted.

## Executed evidence

`make test-integration` started fresh Gitea volumes and executed separate Python processes against
one file database:

| Case | Expected | Observed | Status |
|---|---|---|---|
| Identity/baseline restart | Binding and immutable baseline readable in a new process | Target account ID and original baseline matched | passed |
| Same numeric IDs/namespaces | Instance and target namespaces prevent collision | Unit constraints kept same IDs distinct | passed |
| Confirmed create restart | Second process must not POST again | Same target global ID/index; issue count unchanged | passed |
| Crash after remote success | New process must halt, without known target ID | `executing` became `outcome_unknown`; no second issue | passed |
| Lost successful response | No automatic retry | `outcome_unknown`; no second issue | passed |
| Parallel executors | At most one remote write | Second process blocked by local lock; one issue created | passed |
| Local completion fault | No half mapping | Mapping insert and status update both rolled back | passed |
| Selected drift restart | Drift remains blocking after reread | Drift stayed true; immutable baseline unchanged | passed |
| Control repository change | Must not affect selected baseline | Drift stayed false before selected issue changed | passed |
| Concurrent mapping inserts | DB uniqueness remains authoritative | One insert succeeded; the other reported conflict | passed |
| Newer schema | Fail without replacement | Version 999 rejected and sentinel row remained | passed |

The generated field-level result is
`integration-results/report.json` under `persistent_state_restart`. A passing crash test means the
ambiguous operation was correctly halted; it does not mean that operation was successfully
migrated.

## Offline transport boundary

Each GitFerry installation owns its own SQLite state. The subsequent v1 transport manifest now
carries the issue-scoped subset of these portable facts across that boundary:

- origin instance registration/fingerprint claim and original repository/object namespace;
- global IDs, repository issue indexes, comment IDs, parent relations, and origin timestamps;
- transfer ID/round, selection, comparison ruleset name/version, and immutable baseline digest or
  selected values;
- source-side explicit identity key and intended target-account selector, without passwords or
  tokens;
- prior target mapping facts that must return to the originating installation.

Blueprints remain operator intent, transport manifests remain portable provenance, and SQLite
operations/events remain local execution state. The executed boundary is documented in
[`verification-portable-transport-roundtrip.md`](verification-portable-transport-roundtrip.md);
whole SQLite files are still never synchronized.

## Confirmed limits

- There is no exactly-once guarantee across SQLite and Gitea. A local uniqueness rule cannot remove
  external duplicates that predate confirmation.
- The executor lock protects writers on one local state volume, not several hosts or copied
  databases.
- Automatic reconciliation is not implemented; ambiguous creates need explicit stable-ID evidence.
- Regular Gitea create endpoints still do not restore native historical `created_at`; origin times
  remain separate from native target timestamps.
- Native author restoration still requires an existing mapped account and the previously verified
  act-as mechanism.
- Only the previously tested issue writes and selected fields are journaled. Pull requests,
  reviews, attachments, LFS, wikis, releases, LDAP/AD, and broader permission dimensions remain
  unverified.
