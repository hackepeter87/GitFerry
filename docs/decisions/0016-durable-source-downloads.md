# ADR 0016: Durable source commands and verified archive downloads

Status: accepted through PR #55, integrated as `de6fcf4`; exact-main CI run `44870014502` passed.

## Decision

The admin source-selection screen queues the existing source capture through a small HTTP
boundary. The process-owned plan worker also executes these source commands. Both execution
paths use the existing `OperationExecutor` lock; a CLI command cannot race a background capture
or target mutation. The browser neither performs the capture nor keeps its execution alive.

SQLite migration 10 adds `source_export_deliveries`, referencing the immutable source-export
intent. A separately prepared CLI intent is not automatically queued. HTTP creation records
intent before queueing; interruption in between leaves a visible, explicitly startable intent.
Reusing an export ID with a changed request remains a conflict. The API requires an authenticated
administrator, CSRF protection and an available worker before accepting a queued command.

Delivery transitions are `queued -> running -> ready` or `running -> failed`. A process restart
may resume `queued` or `running`; `failed` requires an explicit retry. Existing source-capture
publication evidence is reused. Before publishing an archive, the worker saves its candidate
SHA-256 and size. Publication is no-replace and fsynced. Recovery accepts an already published
archive only when its bytes match that locally persisted evidence. An unproven file is neither
adopted nor overwritten. The worker does not hold a SQLite transaction during remote reads or
archive construction.

Archives are application-owned files under the persistent work directory's reserved `.downloads`
namespace. Download verifies regular-file type, size and SHA-256 on an opened descriptor and
streams that same descriptor in bounded chunks. The response exposes the archive byte digest
as `X-Archive-SHA256`. It is integrity evidence, not a signature or sender attestation. The
browser uses a normal authenticated same-origin download, not a whole-archive JavaScript blob.

## Boundaries and consequences

This is a local delivery state, not a second transfer engine or a portable execution status.
Application-package v1 and metadata-manifest v1/v2 remain unchanged. The SQLite version is
independent of those formats. The existing operation journal's `outcome_unknown` and
`blocked_parent` states are not reinterpreted: source capture reads data, while target mutations
continue through their existing journal and reconciliation rules.

Confirmed capture and confirmed download are distinct from target import. Recipient registration
does not authorize target identities or writes. Credentials and CA files remain local references.
The package still declares its bounded data scope and missing capabilities; this UI does not
establish complete inventory, LDAP/AD support, native historical timestamps or atomic PATCH
preconditions.

The retained capture plus ZIP increases disk usage. Current clone/output limits still apply;
capacity reservation, retention management, full-operation cancellation and 30 GB acceptance
remain their existing roadmap issues. An interrupted read-only capture may repeat source reads;
it does not retry a target POST. No new queue service or database dependency is introduced.

## Verification

`test_source_export_jobs.py` covers explicit queueing, shared executor exclusion, changed intent,
visible failures, corrupted/unproven archives, schema-9 upgrade and real-process recovery after
archive publication. `test_source_export_api.py` covers the authenticated/CSRF boundary, worker
availability, wake-up, streaming and sanitized errors. Browser and real Gitea execution results
are recorded separately in [testing.md](../testing.md); module tests alone do not qualify the
complete first-import wizard.
