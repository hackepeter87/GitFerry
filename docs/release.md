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

## Current status

The repository remains at `0.1.0.dev0`. The technical 0.1.0 foundation evidence is based on
integrated commit `dedc0778677f1e88eaf1c0baebfee7a1ead0966d`. Hosted run `44808991767`
passed `quality` and `gitea-integration` on that exact clean Linux checkout; its normal and
controlled-failure paths retained the required reports and completed scoped cleanup. Active ruleset
`27751371` requires both contexts on current code, pull requests and resolved threads, with no bypass
actors and deletion/non-fast-forward protection.

The complete acceptance classification, dependent delivery cycles and restart/resume evidence are
recorded in [the 0.1.0 foundation report](verification-0.1.0-foundation.md). The report-integration
merge and its final main check are durable GitHub evidence on Issue #5 because their merge commit is
not knowable from the report's parent revision. No OCI/offline release candidate was built, tagged
or published for 0.1.0; stable publication remains a separate owner decision.
