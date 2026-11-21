# Receive a package and prepare a local first-import plan

The receiving application uses its own SQLite database, local connection profiles and credential
references. A source export contains portable facts, not permission to write to the target.
This operator path supports the shared core; the complete guided browser workflow is still in
development under [Issue #10](https://github.com/hackepeter87/GitFerry/issues/10).

1. Receive the generated archive using the existing `gitferry packages receive` command into a
   new directory under `GITFERRY_PACKAGE_DIR`. Use its directory name as `--package-ref`.
2. Register the known sender installation and approve the local target connection using the
   existing connection workflow. Names and URLs alone do not establish installation identity.
3. Review and admit the files with the exact digest reported by the review:

   ```sh
   gitferry intake review --package-ref received --target-instance target --profile-revision 1
   gitferry intake admit --package-ref received --target-instance target --profile-revision 1 \
     --expected-digest <reviewed-package-sha256>
   ```

4. Review the selection and its prerequisites:

   ```sh
   gitferry intake plan-review --package-ref received --target-instance target --profile-revision 1 \
     --author-credential issue-binding=issue-token-reference \
     --author-credential comment-binding=comment-token-reference
   ```

   Author references require previously confirmed, realm-qualified local identity bindings.
   A missing binding or author credential blocks metadata planning. `--apply-members` enables
   explicitly mapped team memberships; it does not create accounts. `--exclude-metadata` permits
   an explicit structure/Git-only selection. These omissions remain visible in plan limitations.

5. Call `intake prepare-plan` with the same selection arguments and
   `--expected-review-digest <review-digest>`. Review the resulting existing draft plan before
   its separate execution. Changed selection, package bytes or local prerequisites require a
   fresh review. Unknown outcomes require the existing controlled reconciliation procedure.

All intake commands accept `--database` or use `GITFERRY_STATE_DB`; package and work paths use
the existing runtime environment. `gitferry intake list` lists locally admitted receipts. An
admitted receipt is not an applied or completed transfer.

The authenticated REST equivalents are `GET /api/v1/packages` and POST
`/api/v1/packages/{package_ref}/review`, `/admit`, `/plan-review`, `/prepare-plan`. POST requests
require the existing admin session and CSRF token. Common JSON fields are `target_instance_key`
and `profile_revision`; admission additionally requires `expected_digest`, and preparation
requires `expected_review_digest`. Plan options are `include_metadata` (default true),
`apply_members` (default false) and `author_credential_refs` (binding ID to local secret reference).
Requests contain references, never token values. No intake endpoint sends Gitea writes.

Existing manifest-v1/v2 files retain their exact bytes and digests. SQLite schema 9 records local
receipts without changing those formats. There is no signature verification yet: hashes check
consistency, not authenticated sender provenance. See [ADR 0015](decisions/0015-package-admission-and-local-plan.md)
for transaction and trust boundaries.

## Upload through the administration screen

Choose a generated ZIP in **Upload a migration package**, then select **Send package**. The
saved receipt shows the intended recipient, selected repositories, metadata presence, limitations
and byte digests. Refreshing or reopening the screen only reads receipts; it does not submit a
migration. If a reply is lost, reselect the same file and retry with the saved upload ID. For a
complete file left in validation after a process interruption, select **Resume validation**.
An incomplete body requires resending the complete file. Selecting a different file starts a
new receipt; existing receipts remain inspectable.

A ready upload makes its `package_ref` available to the admission workflow above. It does not
approve identities or a target connection, admit transport facts, or write to Gitea. The browser
connection from this file receipt to local identity decisions and plan review remains under
Issue #10. Historical source timestamps are still separate from native target timestamps.

## Upload API

The administrator-only API is under `/api/v1/package-uploads`:

- `GET /api/v1/package-uploads` lists recent receipts.
- `GET /api/v1/package-uploads/{upload_id}` returns one receipt and its summary.
- `PUT /api/v1/package-uploads/{upload_id}` streams one `application/zip` body. Supply
  `X-GitFerry-Archive-Size`; if `Content-Length` is present it must match.
- `POST /api/v1/package-uploads/{upload_id}/resume` explicitly retries validation/recovery with
  an empty JSON object.

Write requests require the authenticated administrator session and CSRF protection. The service
stores the raw ZIP and extracted package under the application-owned work/package directories,
records size and SHA-256 evidence in local SQLite, and exposes a `ready` receipt only after the
existing package/archive validation succeeds.

The shared local executor lock covers receiving and validation. A busy lock is a conflict to
retry later. A receiving timeout is controlled by `GITFERRY_UPLOAD_TIMEOUT_SECONDS`; the archive
size limit is controlled by `GITFERRY_UPLOAD_MAX_BYTES`. These are distinct limits. The protocol
never resumes a partial byte stream: retry the same complete file when the candidate is
incomplete or unavailable.


SQLite schema 11 adds local upload receipts; package v1 and manifest v1/v2 remain unchanged.
See [ADR 0017](decisions/0017-durable-package-upload-receipt.md) for publication and restart
boundaries, and [testing](testing.md) for executed evidence.
