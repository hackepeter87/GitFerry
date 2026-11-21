# Contributing

GitFerry uses issue-first delivery. Public issues and version milestones define scope; the private
GitHub Project adds operational status, priority, type, area, target release, risk, and decision
state without replacing those public records.

## Before implementation

An implementation issue must describe the problem and expected result, scope and exclusions,
acceptance criteria, required tests and evidence, documentation and compatibility impact,
dependencies, and release milestone. Reuse an existing issue when it already owns the outcome.

Create a dedicated branch from current `main` only after the issue is ready. Keep one coherent
capability or user flow in each pull request; do not split work by individual file or helper.
Continue an existing suitable branch/PR and use small commits for its related implementation
steps. Set an elapsed-time boundary for the delivery, including CI waits; exceeding it requires
a preserved checkpoint and a budget-halt report, not an automatically renewed execution period.

## Implementation and review

1. Preserve existing behavior and compatibility unless the issue explicitly changes it.
2. Add or update regression coverage before or with behavior changes.
3. During implementation run affected tests and exercise the complete user flow early. At the
   finished delivery run `make test`; run `make test-integration` for Gitea-facing behavior and
   `make test-integration-failure-contract` when the integration runner changes.
4. Update current-state documentation and `docs/capability-matrix.md` only from executed evidence.
5. Open a pull request linked to the issue and report exact checks, environments, limitations, and
   any remaining manual acceptance.
6. Review the complete current diff separately for correctness, behavior changes, failure modes,
   compatibility, security, and test coverage. New commits invalidate an earlier review.
7. Squash merge only after required checks and review pass, then verify the integrated `main` commit.

Reuse matching automatic CI runs rather than dispatching duplicates. Changed code must never
inherit a stale review or test result. Keep one current checkpoint in the issue with branch/commit,
open criteria, latest tests/review, next action and blockers. Product documentation belongs with
the functional delivery; run-specific evidence belongs in issue/PR records and Actions artifacts.
Post-merge run IDs alone do not justify another documentation PR. These rules do not reduce any
required check or acceptance criterion; control-rule changes still require separate risk review.

Commit messages use `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, or `chore:` prefixes. A mock
test cannot replace a required real-Gitea integration test. Never add productive credentials, real
internal domains, personal data, database exports, package contents, or machine-specific paths.

## Release candidates

Milestone acceptance is separate from implementation review. Candidate artifacts are built once
and identified by source commit and immutable digest. The exact artifacts—not rebuilds—receive the
release acceptance tests. Preparing a candidate does not publish a stable release, update a stable
channel, or authorize a production installation. See [release evidence](docs/release.md).
