# ADR 0015: Portable package facts and locally authorized plans

Status: accepted through [PR #54](https://github.com/hackepeter87/GitFerry/pull/54),
within [Issue #10](https://github.com/hackepeter87/GitFerry/issues/10).

## Context

Approved-source capture now produces a portable first-import package. The receiving application
must turn those files into the existing durable plan without requiring operators to copy origin
IDs, author selectors or Git payload hashes by hand. Receipt cannot authorize a target connection,
identity binding or remote write.

## Decision

`PackageAdmissions` validates the existing application-package-v1 bytes and embedded unchanged
manifest-v1/v2 bytes. Sender and recipient installation markers must match local registrations.
The selected target profile revision and connection identity must already have local approval.
The service reuses the transport admission transaction and immutable origin/replica rules; it does
not add another metadata transfer engine.

Migration 9 adds `package_receipts`, binding an immutable package ID to its exact descriptor digest,
target instance, original local reference and portable document. The original reference records
the first receipt location; another local directory with exactly the same verified bytes may be
used as an alias. Its concrete execution location is retained in the plan request. An alias cannot
replace a different package already received under that name. Payload hashes remain bound by the
descriptor. A duplicate ID with different bytes conflicts. Exact replay is accepted. A new package
ID does not create new object identities or clear existing operation/conflict states. Application,
package, manifest and SQLite versions remain independent; no dependency is added.

Review runs the same fact admission under a rolled-back SQLite savepoint. It has no committed
business-state effects and no network calls, although it briefly takes the local SQLite writer
lock. Admission commits structure origins, remote origin-side copies, embedded transport facts
and the receipt together. An error rolls all of these back. Received claims about previously
unknown local target objects are rejected; files cannot establish a trusted target replica.
Conflicting known structure scopes also fail rather than inventing an origin after a rename.

Received realm descriptions and identity claims remain proposals. Admission does not register
realms, approve identity bindings, create credentials, replace profiles, write Gitea objects or
mark operations successful. Local operators make explicit binding decisions. Technical owners
remain separate from business accounts.

## Plan preparation and execution

A read-only selection translator builds the existing `PlanRequest`. It preserves separate issue
and comment author selectors, permanent parent origins, selected Git payload hashes, structure
origins and omissions. Team membership requires an explicit local binding; metadata authors also
require a local credential reference. Missing mappings do not fall back to the importer. Selecting
structure without membership or metadata is explicit and appears in plan limitations.

The review digest binds package bytes, local target approval and profile revision, selection,
credential references and observed prerequisites. Preparation checks that digest again, requires
a matching committed receipt, and uses the existing plan/journal implementation to publish a
draft. It does not queue or execute the plan. Receipt and draft preparation are separate local
transactions: admitted facts are not a successful migration. Existing operation preparation may
persist journal intent before a later plan-preparation failure; existing stable identities make
that intent reusable rather than an external write.

Before execution the Gitea adapter revalidates the package receipt and bytes, then performs its
existing target, credential, relationship, drift and storage checks. Existing writer exclusion,
parent dependencies, durable confirmation and unknown-outcome rules remain in force. A new
wrapper or receipt never authorizes another POST for an unknown outcome.

## Limits and verification

This is first-import planning, not a general merge, return-transfer or directory-provisioning
engine. Nonempty issue labels remain visibly unsupported by this application path until label
provisioning is implemented. Other omitted content remains listed and inventory completeness
stays false. No signatures, native historical timestamp restoration, atomic comment PATCH
precondition or general exactly-once guarantee is claimed.

`test_package_admission.py` covers rollback, stale local approval, receipt identity, actual
child-process receipt replay, explicit author prerequisites, no-user planning and unknown-outcome
retention under repackaging. Adapter/selection tests cover HTTP authentication, CSRF and CLI
contracts. Real Gitea evidence is recorded in `docs/testing.md` only after execution. The complete
browser workflow and Issue #10 acceptance remain separate outstanding work.
