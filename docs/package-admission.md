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
