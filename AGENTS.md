# GitFerry agent rules

The project-owner requirements, public GitHub issues/milestones, and `docs/requirements.md` define
scope. Never claim a migration capability unless a real Gitea integration test supports it.

## Required workflow

- Use the installed, version-matched ProjectAtlas skill and MCP tools before broad source reads. Run `atlas_init` only when project-local state is absent, then use `atlas_watch_once` after edits.
- Treat the current release milestone and its issues as the executable scope. GitLab support and
  productive-data migration remain outside the approved roadmap.
- Work in small testable slices. Do not weaken a test or expectation to make a run green.
- Before implementation, ensure a public issue defines the problem, result, scope, exclusions,
  acceptance criteria, tests, documentation impact, dependencies, and release milestone. Keep
  private operating detail out of public artifacts.
- Use one branch and pull request for a coherent user outcome, with small testable commits;
  keep related implementation layers together. Reuse the existing delivery branch/PR.
  A current-commit AI review,
  required CI, documentation, compatibility evidence, and integrated-main verification are
  mandatory before closing the issue.
- Commits, pushes, pull requests, and regular merges are authorized for the owner-approved
  roadmap. Stable release publication, production changes, paid services, material scope changes,
  and changes to other repositories or accounts require separate owner approval.
- Do not weaken repository rules, required checks, review controls, or acceptance criteria to
  approve your own change. Control-rule changes require a separate risk review.
- Treat blueprints, package archives, API data, paths, and filenames as untrusted input.
- Never put credentials, tokens, private keys, internal hostnames, or real directory data in source, fixtures, logs, reports, or exported blueprints.
- Use only synthetic test identities and data.
- Do not disable TLS verification as a normal mode. Support explicit CA references instead.
- Do not modify Gitea databases directly as a migration mechanism.

## Agent roles and delegation

- The main agent owns planning, architecture, integration, milestone acceptance, and unclear failures. Use the repository model/profile defaults in `.codex/`.
- Delegate a substantial, bounded routine task when doing so reduces total model work or elapsed time. Bundle related routine work; do not spawn an agent for a trivial step.
- Use `explorer` for read-only discovery, `mechanic` for pattern-driven edits inside an explicit file boundary, and `reviewer` for targeted risk review. Prefer deterministic tools, formatters, codemods, or scripts for fully specified transformations.
- Give each subagent only the relevant files, constraints, expected result, and checks. Require a short report covering changes or evidence, checks, and open problems.
- Keep at most three subagents active in addition to the main agent. Assign non-overlapping file ownership; sequence dependent or overlapping work.
- If the configured role model is unavailable, name the replacement and its changed cost/capability class before use. Never allow an economical role to inherit the main model silently.
- After one failed attempt, narrow or correct the task if evidence supports it. After two failed attempts on the same problem, the main agent reassesses and takes over or chooses a different approach.
- The main agent reviews delegated results, integrates them, runs the applicable aggregate gates, and remains responsible for every support claim.

## GitHub delivery and recovery

- The public issue and milestone are authoritative for scope. The private GitHub Project adds
  operational fields and must not contradict them. Status flows through Backlog, Ready, In
  Progress, In Review, and Done.
- On start or resume, inspect the working tree, current branch, open pull requests, active Project
  items, and required checks before selecting work. Reuse an existing issue/branch/PR; never create
  parallel work for the same acceptance criteria.
- Keep work resumable in GitHub: update issue/PR state, concrete blockers, current review findings,
  and next safe action before a natural execution boundary.
- Maintain one current issue checkpoint: branch/commit, open acceptance criteria, latest relevant
  tests/review, next action, and actual blocker. Do not repeat a full repository analysis on resume.
- Keep product requirements, architecture, usage, capabilities and limits in repository docs;
  keep commit/run IDs, review findings and acceptance evidence in the issue/PR and Actions artifacts.
  Never open a follow-up documentation PR solely to record post-merge run IDs.
- Set an elapsed-time execution boundary before each coherent delivery, including CI waits.
  At a budget boundary preserve work, record usable results/gaps and stop resource-intensive work;
  do not silently start another timebox. After two failed repairs, reassess and change the approach.
- During implementation run affected tests and exercise the full user flow early. At delivery run
  all applicable gates, current-commit review and integrated-main verification. Reuse a valid
  automatic CI run; do not dispatch a duplicate for the same relevant commit/configuration.
- Review the actual current diff, requirements, and test evidence. A summary or stale review tied to
  an earlier commit is not approval.
- Prefer squash merge after required checks and review pass. Verify the resulting `main` commit;
  a green pull-request commit is not proof for a different merge commit.
- Build release candidates once. Record source commit, version, digest, and evidence; do not pass an
  untested rebuild off as the accepted artifact.

## Test truthfulness

Statuses are `passed`, `failed`, `blocked`, and `not-implemented`. A started container is not proof that migration works. A test not executed is never `passed`.

Run from the repository root:

```text
make doctor
make test
make test-integration
make test-clean
```

`make test-integration` owns only Docker Compose resources whose project name starts with `gitferry-it-`. `make test-clean` must never prune global Docker resources or delete unrelated resources.

Before reporting completion, run formatting, lint, type checks, unit tests, frontend build, and the applicable integration probe. Record exact Gitea versions and concrete results in `docs/capability-matrix.md` and `docs/testing.md`.
