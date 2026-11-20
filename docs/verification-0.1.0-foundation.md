# Issue #5: 0.1.0 Foundation Acceptance

Status: technically accepted as foundation evidence. This report does not approve a stable
release, and it does not claim 0.2 user-flow acceptance.

## Scope and integrated result

The accepted integrated-main baseline is `dedc0778677f1e88eaf1c0baebfee7a1ead0966d`.
PR #42 is at head `168cadc28456be2d9faf8111335eccde9002879e`; its current-head review
reported no findings. Run `44808494083`
passed `quality` in 1m13s and `gitea-integration` in 2m43s. The integrated-main run
`44808991767` passed both `quality` and `gitea-integration`.

The two current-diff review cycles are recorded as follows:

- PR #41 (feature), merged as `f4dc16b`: the independent Codex reviewer subagent
  (`gpt-6-astra`, high reasoning) found and the change fixed stable-ID and tag bugs. The reviewer
  then re-read final head
  `64bf3dd5bc4ddc06e57bfe2bfd09f8d0fb98a14e`, ran 64 focused tests plus missing/replaced
  structure and permission-drift probes, and reported no findings. Current-head run
  `44805207547`; main run `44805706378`.
- PR #42 (evidence/test contract), merged at `dedc077`: an independent Codex reviewer subagent
  (`gpt-6-astra`, high reasoning) found and the change fixed label and owner gaps, then reported no
  findings on final head `168cadc28456be2d9faf8111335eccde9002879e`. Current-head run
  `44808494083`; main run `44808991767`.

## Evidence classification

- **Locally executed:** on the current acceptance branch, `make doctor` passed on Darwin/arm64 with
  Docker Desktop Linux/arm64 29.9.3 and Compose 5.7.0 after the sandboxed first attempt was denied
  access to the Docker socket. `make test` passed Ruff, formatting, MyPy, 144 backend tests and the
  frontend production build. `make test-clean` passed. Earlier capability-specific local runs
  remain documented separately; this report does not relabel them as a fresh current-branch
  integration run.
- **Hosted executed:** the successful current-head and integrated-main runs above. Fresh GitHub
  checkouts executed `make doctor`, `make test`, `make test-integration`,
  `make test-integration-failure-contract`, and the scoped `make test-clean` path. Artifact
  `14664143681` shows a clean Linux/x86_64 checkout at `dedc077`, Docker Linux/amd64 28.0.4
  without emulation, and Gitea 1.27.2 at exact digest
  `d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`. It records 19
  required checks passed, normal cleanup passed, and outer cleanup passed. A controlled failure
  exited 86, kept diagnostics, and passed both cleanups.
- **Configured-only:** Ruleset `27751371` is active on `main` with exact checks `quality` and
  `gitea-integration`, strict current-code, required PR/resolved threads, no bypass, and
  deletion/non-fast-forward protection. This describes enforcement configuration, not a product
  migration capability.
- **Supported-with-limits:** only the bounded foundation capabilities classified in the capability
  matrix are accepted. Their field and workflow limits remain part of the support contract.
- **Blocked:** the first fresh-context GitHub query was network-blocked; it was not counted as
  success. The explicitly authorized read-only retry passed and preserved the existing work.
- **Not yet tested:** 0.2.0 user flows and later matrix rows remain unimplemented or unverified as
  recorded in their owning issues. No productive-data migration or GitLab support is claimed.

## Milestone state and limits

Issues #1–#4 are closed/Done; Issue #5 is the sole open milestone item. A fresh-context resume
probe had a blocked first GitHub query; an explicit read-only retry succeeded, finding branch
`release/0.1.0-acceptance` at `dedc077` and Issue #5 In Progress, with no duplicate created.

### Controlled resume transcript

The sanitized invocation and output are also retained in the
[Issue #5 evidence comment](https://github.com/hackepeter87/GitFerry/issues/5#issuecomment-7049764078).

The resume agent was started with `fork_turns=none`, low-cost explorer model configuration and only
the repository path plus a read-only discovery contract. It received no parent conversation or
Python objects and was prohibited from editing, creating, commenting, committing, pushing or
changing GitHub state.

Its first result read AGENTS.md and ProjectAtlas, then found a clean
`release/0.1.0-acceptance` branch equal to `main`/`origin/main` at `dedc077`; it selected Issue #5
as the next likely unblocked item. GitHub API calls failed in the restricted network, so that part
was reported `blocked` rather than inferred.

The explicit follow-up authorized read-only GitHub access. In that snapshot, the same agent reported
Issues #1–#4 closed and Done, Issue #5 as the sole open 0.1.0 item and In Progress, run
`44808991767` green for both required checks, and Ruleset `27751371` active without bypass. The
only open pull request was unrelated Dependabot PR #38. It instructed the main agent to reuse the
existing acceptance branch and create no issue, branch or PR. No resume-probe write occurred. Issue
#5 subsequently moved to In Review when PR #43 opened.

No 0.1 artifact candidate has been built or published. This is technical foundation acceptance
only; stable release publication and 0.2 user-flow acceptance remain open work.

## Checks and open issues

Reported checks: current-head review, hosted `quality`, hosted `gitea-integration`, integrated-main
verification, required ruleset enforcement, artifact cleanup, and controlled-failure diagnostics
all passed as stated above. The first fresh-context GitHub query was blocked, but its explicit
read-only retry passed; it remains a recorded recovery event, not a product failure.

Open issue: Issue #5 remains In Progress and is not closed by this report. No additional failing
or not-yet-tested capability is promoted to passed.
