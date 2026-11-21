# Delivery and release evidence

## Planning and execution

GitFerry work is tracked in public issues and one concrete SemVer milestone per delivery. Each
issue owns a coherent capability or user flow. The private **GitFerry Roadmap** Project supplies
Backlog → Ready → In Progress → In Review → Done status and operational metadata; it must not
contradict the issue or milestone.

For every change:

1. define the issue and dependencies;
2. branch from current `main`;
3. implement and run the issue-specific local gates;
4. open or update the linked pull request;
5. run hosted checks for the current commit;
6. obtain a separate AI review of the actual diff and evidence;
7. resolve findings and rerun invalidated checks/review;
8. squash merge and verify the integrated `main` result;
9. close the issue only after its acceptance criteria and documentation are complete.

Repository rules, not prose alone, enforce stable required checks after the initial hosted runs
establish their names. An implementation may not weaken its own gate. Control changes receive a
separate risk review.

## Recovery and continuation

On every start or resume, inspect the working tree and branch, open pull requests, active Project
items, issue dependencies, current check conclusions, and unresolved review findings. Continue an
existing branch/PR before selecting the highest-priority Ready issue. Durable GitHub state prevents
duplicate work; a label or milestone by itself is not an executing agent.

The repository configuration describes how an authorized Codex client allocates main-agent and
subagent work. It does not install an unattended paid agent service. The active authorized client
performs pushes and pull requests, which allows ordinary `pull_request` and `push` workflows to run;
GitHub Actions does not create code pull requests using `GITHUB_TOKEN`, avoiding the event-chaining
restriction of automation-created events.

## Candidate contract

A release candidate is not a release. Candidate preparation must:

- start from an accepted integrated commit;
- build OCI and offline artifacts once;
- record application, manifest, and SQLite schema versions independently;
- record source commit, OCI labels, byte or registry digests, SBOM, and relevant test evidence;
- install and test the exact built artifacts by immutable digest;
- retain diagnostic and compatibility evidence with the candidate identity.

No later rebuild inherits the earlier evidence. Stable publication, a stable channel, `latest`, or
production activation requires the separate owner decision reserved in the project mandate.

## Historical 0.1.0 foundation

The 0.1.0 foundation used application version `0.1.0.dev0`. The technical 0.1.0 foundation evidence is integrated at
commit `07fe353a896b83a29ee8013bddb80ef638e50c56`. Hosted run `44811562483` passed
`quality` and `gitea-integration` on that exact clean Linux checkout; artifact `14668890407` records
all required checks, controlled failure exit 86 with diagnostics, and successful run-local and
outer cleanup. Active ruleset
`27751371` requires both contexts on current code, pull requests and resolved threads, with no bypass
actors and deletion/non-fast-forward protection.

The complete acceptance classification, dependent delivery cycles and restart/resume evidence are
recorded in [the 0.1.0 foundation report](verification-0.1.0-foundation.md). The report-integration
merge and its final main check are durable GitHub evidence on Issue #5. No OCI/offline release
candidate was built, tagged or published for 0.1.0; stable publication remains a separate owner
decision.


## 0.2 first-import candidate

The application candidate uses Python version `0.2.0rc1` and equivalent frontend SemVer
`0.2.0-rc.1`. The API reports the application version instead of a separate scaffold constant.
Manifest v1/v2 and SQLite schema 12 are unchanged. This version is a candidate identifier,
not stable publication. Exact candidate records and acceptance belong to Issue #11 and its
Actions artifacts; the issue remains open until all required evidence is inspected.

Use the integrated commit to build once, keep the resulting image identity through the complete
runtime/reference-Compose/browser/Gitea tests, then retain that same image as an offline OCI
archive. Record the image configuration digest and archive byte SHA-256 separately. Include a
package inventory/SBOM from the tested image, build labels, platform, versions and the successful
report. A separately built architecture has its own identity and evidence. The documented
Compose installation must use the accepted image, not silently rebuild it. No `latest` or stable
release is published by milestone acceptance.


## 0.3 directory candidate

The application candidate uses Python `0.3.0rc1` and frontend SemVer `0.3.0-rc.1`.
Manifest v1/v2 bytes and digests remain unchanged. SQLite schema 15 adds local identity decisions,
directory settings and bootstrap execution records through the existing migration chain; these
versions are independent. Candidate acceptance requires the same build-once evidence described
above plus an upgrade from the accepted 0.2 image/state, retained bindings/baselines/unknown outcomes,
and the real directory desktop workflows. Issue #16 owns exact image and run evidence. Merely
changing the version does not accept or publish a release.
