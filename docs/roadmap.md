# GitFerry release roadmap

Public GitHub issues and version milestones are authoritative for executable scope. The private
**GitFerry Roadmap** Project adds operational status and risk metadata without replacing them. No
calendar dates are invented. A milestone closes only after its integrated user flow and failure
paths pass on the exact accepted revision or artifact.

## 0.1.0 — GitHub foundation and autonomous delivery

Adopt the existing implementation without recreating it; execute hosted Linux CI; enforce stable
pull-request gates; complete the product capability baseline; and prove resumable issue-first
delivery through two dependent implementation cycles.

- [#1](https://github.com/hackepeter87/GitFerry/issues/1) adopts the local application and process
  foundation.
- [#2](https://github.com/hackepeter87/GitFerry/issues/2) runs and enforces hosted CI.
- [#3](https://github.com/hackepeter87/GitFerry/issues/3) completes the requirement/capability
  evidence matrix.
- [#4](https://github.com/hackepeter87/GitFerry/issues/4) composes the bounded durable provisioning
  service. The application-service flow, independent review, hosted CI and integrated-main
  verification are complete on merge commit `f4dc16b260ed96d48a8194f6d12b2d867dbc3b86`.
- [#5](https://github.com/hackepeter87/GitFerry/issues/5) owns integrated milestone acceptance and
  the resume proof.

The workflow definition alone is not hosted-CI evidence. The milestone requires actual GitHub
Actions runs and effective repository rules.

The capability matrix uses one owning issue per row. A current bounded implementation can therefore
remain `supported-with-limits` while its owner delivers or accepts the complete product contract in
the target release; the roadmap link is not evidence that the future capability already exists.

The integrated foundation through `dedc0778677f1e88eaf1c0baebfee7a1ead0966d`, its two reviewed
delivery cycles, hosted checks, repository rules and controlled resume exercise are classified in
the [0.1.0 foundation acceptance report](verification-0.1.0-foundation.md). This milestone is an
engineering foundation and does not claim the 0.2.0 provisioning user flow or a stable release.

## 0.2.0 — First operable provisioning flow

Deliver persistent OCI operation, protected administration, secret-reference/TLS connection
profiles, a durable execution service, and a guided first-import UI. The integrated acceptance must
provision selected repositories to an empty functional target without manual manifest or SQLite
editing and preserve confirmed progress across browser and process restart.

Owning issues: [#6](https://github.com/hackepeter87/GitFerry/issues/6)–[#11](https://github.com/hackepeter87/GitFerry/issues/11).

Issue #9 is accepted through PR #50, integrated as `1b0bed5` and verified by hosted-main
run `44851149501`. The persisted-plan Gitea execution, API/CLI paths, plan UI and browser
disconnect flow are covered by their documented real and simulated evidence. Issue #10 is
now active for the complete first-import workflow. Source selection and the package file boundary
are integrated (PRs #51 and #52). The source-export slice now connects selected source reads and
Git bundles to that package without changing metadata v1/v2 bytes. PR #53 is integrated as
`5c7d303` with hosted-main run `44862775964` passing all 23 mandatory checks. Local
receipt/admission and translation into the existing durable plan are in development; local
identity decisions and the complete wizard remain
open acceptance work; this does not close Issue #10 or milestone 0.2.
The [plan guide](transfer-plans.md) describes the current boundary without claiming a complete
first-import wizard.

## 0.3.0 — Identities, directories, and effective permissions

Deliver explicit realm-qualified mapping and conflict administration, a supported Gitea
authentication-source bootstrap adapter, organization/team/repository rights, late mapping, and
separate real LDAP and AD qualification. OpenLDAP evidence is never treated as complete AD
evidence. Both temporary-offline and no-user-transfer provisioning modes receive functional-user
acceptance.

Owning issues: [#12](https://github.com/hackepeter87/GitFerry/issues/12)–[#16](https://github.com/hackepeter87/GitFerry/issues/16).

## 0.4.0 — Agreed data-scope completeness

Inventory every selected class and implement or visibly constrain the agreed transfer scope for
ordinary Git, LFS, wikis, issues, labels, comments, attachments, releases, pull requests, reviews,
and other discovered metadata. Missing package entries never imply deletion, and an incomplete
transfer is never reported as complete.

Owning issues: [#17](https://github.com/hackepeter87/GitFerry/issues/17)–[#21](https://github.com/hackepeter87/GitFerry/issues/21).

## 0.5.0 — End-to-end return transfer

Unify Git and supported metadata in one guided A → B → A flow. Preserve origins and local trust,
compare the original central baseline independently from the confirmed offline import state, halt
on relevant drift or unknown outcomes, and keep control repositories unaffected. Replay, restart,
or a new package identity must not bypass completed work or conflicts.

Owning issues: [#22](https://github.com/hackepeter87/GitFerry/issues/22)–[#26](https://github.com/hackepeter87/GitFerry/issues/26).

## 0.6.0 — Operations and transport hardening

Add a signed outer envelope without changing manifest-v1/v2 bytes; local signer trust; streaming
capacity and retention controls; safe cancellation/resume; backup, restore, and upgrade; and an
approximately 30 GB practical qualification. Restore must retain origins, bindings, baselines,
conflicts, and unknown outcomes.

Owning issues: [#27](https://github.com/hackepeter87/GitFerry/issues/27)–[#32](https://github.com/hackepeter87/GitFerry/issues/32).

## 1.0.0 — Acceptance-ready application

Complete fresh-install and operating documentation; exercise every promised UI workflow on a
fresh installation; and build OCI/offline candidates once. Record source commit, versions,
digests, SBOM, compatibility, and test evidence, then test those exact artifacts. The resulting
technical candidate is not a stable release.

Owning issues: [#33](https://github.com/hackepeter87/GitFerry/issues/33)–[#36](https://github.com/hackepeter87/GitFerry/issues/36).

Stable publication, stable channels, `latest`, and productive installation remain a separate owner
decision after technical candidate acceptance.

## Persistent product limits

Until replaced by field-specific real-instance evidence:

- GitFerry provides no general exactly-once guarantee across SQLite and Gitea.
- Regular create endpoints do not restore historical native timestamps; origin timestamps remain
  separate facts.
- The read-before-PATCH comment check is not an atomic Gitea server-side version precondition.
- Unsupported or untested object classes cannot be described as transferred.
- Transport integrity hashes do not authenticate origin until the signed envelope is delivered.
