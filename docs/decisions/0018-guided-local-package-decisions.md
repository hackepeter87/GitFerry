# ADR 0018: Guided local package decisions

Status: proposed under [Issue #10](https://github.com/hackepeter87/GitFerry/issues/10).

## Context

A received package supplies sender, realm and author claims, but cannot approve its own
identities or grant target access. Administrators need to resolve these prerequisites through
the application without editing SQLite or rewriting the portable manifest.

## Decision

The authenticated package API exposes read-only prerequisites and explicit local confirmations
for the sender installation, realm registration and identity binding. Every decision is tied
to the reviewed package digest, selected target installation and approved profile revision.
Sender registration does not contact the source; it records an operator decision, not a
cryptographically authenticated provenance claim. Realm registration does not provision LDAP.

Account checking uses the locally approved target profile and configured CA. It reads the
target account and, when supplied, checks the author credential against that account. Native
account IDs cross the browser boundary as decimal strings. A separate binding confirmation
rechecks the account and rejects administrators, changed IDs and contradictory bindings.
The selector includes realm, login, external identifier and OU path. Equal names alone never
create a binding. Secret references are local; token values never enter these requests.

The existing StateStore registration and binding methods can join a caller's transaction.
The service rechecks local authority within that transaction before recording a confirmation.
Remote reads finish before acquiring that SQLite write transaction. No remote account or
permission mutation occurs during these decisions. External account changes after verification
remain possible; execution repeats the existing target/author preflight before writes.

The browser then uses the existing admission, selection review, draft preparation and durable
plan execution services. Admission records transport facts and can precede author binding.
Missing authors block a metadata plan, while explicitly excluding metadata and memberships
permits the existing structure/Git-only mode. The omissions remain visible in the plan.
Changing options or credentials invalidates the browser's selection review. The backend still
checks the exact review digest; browser state is not an authorization mechanism.

## Compatibility and alternatives

There is no new mapping engine, operation journal or database migration. SQLite remains at
schema 11; application-package v1 and manifest v1/v2 retain their bytes and identities.
Automatically accepting transported bindings was rejected because it would cross the local
trust boundary. A second UI-only planner was rejected because it would diverge from CLI/API
execution, conflict detection and controlled restart.

## Verification and limits

Focused tests exercise rejected bindings, changed authority, transaction rollback, administrator
authentication and CSRF. Browser tests distinguish receipt/admission from permission to execute.
The real integration driver uploads the generated file to the separate target context, makes
local decisions through the UI, runs the existing worker and checks remote Git and metadata.
Executed results, including failures, belong in [testing](../testing.md).

This does not establish real LDAP/AD integration, full inventory, large-transfer capacity,
historical native timestamp restoration or atomic comment precondition/PATCH semantics.
