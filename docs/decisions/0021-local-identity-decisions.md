# ADR 0021: Local identity decisions and target account verification

Status: accepted for implementation; directory and effective-access acceptance remains in #12–#16.

## Decision

Identity administration and package admission use the same immutable `identity_bindings`
records. A separate review record does not grant access or rewrite an author. It records a
qualified source selector, a proposed native target account, optional informational email,
intended issue/comment/team prerequisites, and locally approved connection evidence.

SQLite migration 13 adds candidates and append-only decision events. Candidate account IDs
are transported to the browser as decimal strings. Source identity, target instance and native
account are immutable. Multiple explicit candidates may be inspected without choosing one by
name or email. The existing unique binding constraints still permit only one binding per
qualified identity/target and one source identity per target account.

Approval re-reads the target through the existing strict TLS/profile boundary. The local
administrator account ID is taken from the authenticated session, never a request field. The
CLI records host-local authority separately. Binding insertion and the decision event commit
in one short SQLite transaction. A savepoint removes partial binding work before recording a
binding conflict. Stale revisions cannot overwrite a concurrent decision.

Rejected/conflicted decisions require explicit reopening and another native account check.
Reopening may update connection evidence but cannot redirect the account or identity.
Package admission cannot bypass these decisions through another package ID. Existing approved
bindings remain compatible; no manifest or application-package format changes are required.

## Limits

A typed SID/external ID or OU is a local claim, not proof of LDAP/AD authentication. Verified
native account ID/login and non-admin status are separate from directory qualification. Live
account checks are not an atomic Gitea version precondition; another administrator can change
an account afterwards. Transfer operations retain their own prerequisite checks and journal.

Prerequisite flags explain the proposed use. They are not a second role/authorization system:
only an explicitly reviewed transfer plan grants a team membership or writes metadata. No
source account creation, name/email matching, rights revocation or directory administration is
implied by a binding. Secrets remain in local references and are not candidate fields.

## Verification

The identity service/application tests cover native ID preservation, CSRF/session boundaries,
local actor attribution, conflict rollback, rejected-package bypass, explicit reopening, CLI
reuse, actual subprocess restart and competing subprocess decisions. Real directory and
functional permission evidence must be recorded separately in the delivery issue and Actions
artifacts before accepting 0.3.
