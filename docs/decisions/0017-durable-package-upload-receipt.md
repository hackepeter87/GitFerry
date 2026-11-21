# ADR 0017: Durable package upload receipt

Status: proposed under [Issue #10](https://github.com/hackepeter87/GitFerry/issues/10).

## Context

The first-import package is a ZIP transport envelope. The HTTP boundary needs to receive that
file durably without making the upload itself a target admission, identity approval or Gitea
operation. A browser or process restart must leave inspectable local evidence rather than an
implicit claim that the package was accepted.

## Decision

Administrators upload an `application/zip` body through `/api/v1/package-uploads/{upload_id}`.
The endpoint requires authentication and administrator CSRF protection. The caller supplies the
declared archive byte size and the service streams the body to an application-owned work
directory. It hashes and fsyncs the complete file, then publishes it without replacing an
existing file. Upload storage is not a second package format: the ZIP is validated with the
existing package/archive receiver before the receipt becomes `ready`.

SQLite migration 11 adds `package_uploads`. It records the upload ID, declared size, archive
SHA-256, validated package digest, summary, status and error code. The status flow is
`receiving` -> `validating` -> `ready`, with `failed` for a handled interruption or rejection.
A hard process exit can leave `receiving` or `validating`; these remain unconfirmed until an
explicit retry/resume verifies durable files.
The archive and extracted package remain files; SQLite stores their durable evidence and summary,
not their bytes.

The existing `OperationExecutor` lock covers receiving and validation, including the HTTP body
write. This serializes the upload with CLI, worker and target operations on the same local state
volume. The lock does not grant any remote authority. Upload and validation perform no Gitea
writes, do not register target identities, and do not create or execute a migration plan.

After a crash, recovery is explicit: an administrator calls
`POST /api/v1/package-uploads/{upload_id}/resume` with an empty JSON object. Recovery verifies
the saved size and digest and adopts only locally proven files, then reruns package validation.
The protocol does not support partial-byte resume or append-at-offset. If the candidate is
incomplete, absent or changed, the operator must retry the same file; a different size or digest
for an existing upload ID is a conflict.

## Limits and boundaries

`GITFERRY_UPLOAD_MAX_BYTES` bounds the declared and received archive size (default 64 GiB,
maximum 64 GiB). `GITFERRY_UPLOAD_TIMEOUT_SECONDS` bounds only HTTP body reception (default 900
seconds, maximum 24 hours). Package inspection and archive validation are separate processing
steps and are not silently converted into a larger receiving timeout.

Application-package v1, manifest-v1 and manifest-v2 bytes remain unchanged. A `ready` upload is a
durable local receipt, not package admission and not a successful migration. The complete Issue
#10 browser wizard and productive-data migration are not delivered by this slice.

## Verification

The focused upload tests exercise actual process exits after archive and package publication,
new-process recovery from persisted file evidence, conflicting retries, timeouts and authority
separation. Browser tests cover explicit retry, reload without writes, multiple files and stale
responses. The existing isolated source/target probe now receives through the managed admin API
before explicit local admission. Executed results and their limits are recorded in
[testing](../testing.md); this slice does not complete the first-import browser flow.

Capacity management, automatic retention of files left by an abrupt extraction crash and the
30 GB qualification remain separate roadmap work. Upload validation uses bounded file reads;
this alone is not a complete large-transfer capacity guarantee.
