# Selected source export

The source-selection screen and operator CLI capture a bounded selection from a locally approved
Gitea source into an owned application package. Current exports bind to a confirmed
[selected-content inventory](content-inventory.md); legacy requests remain explicitly bounded
selections. See [ADR 0014](decisions/0014-read-only-source-capture.md) and
[ADR 0016](decisions/0016-durable-source-downloads.md).

## Prerequisites

Use the existing [connection setup](connections.md) to register, check and approve the source.
The recipient's stable instance key and installation marker must already be registered locally;
the source exporter needs no recipient credential or network connection. Repository names and
native IDs come from the source catalog. Registering a URL alone is not sufficient identity.

The configured `GITFERRY_STATE_DB`, `GITFERRY_PACKAGE_DIR` and `GITFERRY_WORK_DIR` are persistent
owned local paths. Existing credential and CA references stay local. No secrets are included
in the package, request record or CLI result.

## Export through the application

Sign in as administrator, select an approved source connection, browse its organizations and
select repositories. Choose the recipient registration and metadata/member options, then create
and queue the export. Current requests also bind the exact ready inventory ID, digest, selected
repositories and classes. A later changed profile or source fact requires a fresh inventory;
recipient registration is local context, not target approval.

Saved exports survive page reload and process restart. The background worker continues without
an open browser. `prepared, not queued` requires Start; `failed` requires investigation and an
explicit retry. `ready` exposes a verified archive download, byte size and SHA-256. These states
describe source packaging, not successful target migration. Refresh exports to retrieve current
status if automatic polling has stopped. An uncertain create response must be retried with the
same saved request ID, not treated as proof that no command exists.

The protected `/api/v1/exports` endpoints provide list/create, recipient registrations, status,
explicit queueing and download. Writes require the existing admin session and CSRF header.
Download streams from owned persistent storage; it does not load the full package into browser
JavaScript memory. The work directory retains both download evidence and the ZIP. Do not delete
or replace confirmed files as a retry procedure.

## CLI capture and inspect

For a synthetic locally approved source at profile revision 1 and a selected repository with
native ID 7:

```sh
gitferry exports prepare --source-instance source --source-revision 1 \
  --recipient-instance target --repository synthetic/selected=7 \
  --include-metadata --include-members --inventory-id selected-inventory \
  --inventory-digest <inventory-content-digest> --export-id selected-export
gitferry exports show --export-id selected-export
gitferry exports run --export-id selected-export
gitferry exports list
```

Repeat `--repository` for each selected repository. Omitting `--include-metadata` or
`--include-members` explicitly excludes those data; it never deletes anything. Current requests
require the exact ready inventory ID and digest; `prepare` only records reviewed local intent.
`run` performs source reads and produces
`$GITFERRY_PACKAGE_DIR/selected-export`. A `captured` result means the files and local source facts
are confirmed, not that target import has happened. Inspect and pack those files using the
existing `packages verify` and `packages pack` commands (`--help` describes their path arguments).

Run again with the same ID to verify a confirmed artifact or recover a publication interrupted
before the local completion commit. A new process can finish that recovery without contacting
the source. A changed selection needs a new export ID. Changed package bytes, stale local
approval, unresolved origins or source drift produce a failure requiring investigation; editing
the database or replacing package files is not a recovery procedure.

## Captured scope and limitations

The package contains selected organization/repository identities, representable read/write
teams, optional observed member identities, ordinary Git refs/bundles, and selected inventory
facts. Authors are observed native Gitea identities, separately qualified from the
technical reader. They are not approved target bindings. In the generated `gitea-local` realm,
`ou_path: /` denotes the native account namespace root, not an LDAP directory path.

Unsupported classes, label assignments, split/additional team units, direct collaborators and
technical-owner teams are explicitly reported and can block selected transfer. LFS and other
content remain unknown unless separately qualified. Existing historical timestamp and
comment-update concurrency limits continue to apply.

The export process re-observes selected facts before and after source capture. Changed IDs,
relationships, source facts, inventory digest or package intent fail the export. This is not a
signature, a hard quota or a 30 GB qualification.

The service compares two selected metadata/structure observations and checks Git refs around
capture. This detects observed selected drift; it is not a server-wide transaction or a guarantee
against concurrent edits between observations. Repository selection, response sizes, pagination
and metadata counts have explicit bounds. A bound violation fails the capture, never truncates
silently. Large-selection and 30 GB qualification are still roadmap work.

### Git storage guards

`GITFERRY_EXPORT_MAX_GIT_BYTES` defaults to 1 GiB and must be a positive integer. The exporter
checks a completed clone before producing its bundle and limits the combined exported bundle
bytes. Bundle generation/verification and remote Git subprocesses have 60-second timeouts.
These are post-transfer/output guards, **not a disk quota during clone reception**. Use a
capacity-controlled runtime volume: temporary Git storage is not strictly bounded by the current
subprocess interface. Raising this setting does not qualify large exports or 30 GB operation.
Strict storage isolation and capacity qualification remain milestone 0.6 work.
