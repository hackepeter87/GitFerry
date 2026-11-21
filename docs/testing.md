# Testing strategy and execution

## Layers

- Unit: blueprint/schema rules, realm/SID/OU identity ambiguity and collisions, SQLite migrations/constraints, operation transitions, selected issue baselines, manifest/path validation, and drift decisions.
- API: FastAPI request/response behavior with no external service.
- Integration: two real Gitea 1.27.2 containers, synthetic seed data, Git bundle and metadata probes, plus file-backed SQLite workers restarted or killed as separate operating-system processes.
- OCI lifecycle integration: inspect the built image, start it non-root on a fresh named volume,
  retain exact mappings/baselines/journal/conflicts across container replacement and SIGKILL,
  observe graceful SIGTERM shutdown, and require corrupt/newer-schema/read-only/symlink starts to
  fail without secret leakage or database replacement.
- Portable transport integration: two additional GitFerry containers with independent named
  SQLite volumes and disjoint internal networks; the harness copies only immutable transport files.
- Browser E2E: Playwright drives the authentication contract against real local FastAPI processes
  and file-backed SQLite stores. It covers the bootstrap prerequisite, keyboard login, denied
  unauthenticated API access, logout, deterministic server-side expiry, host recovery and durable
  revocation. Backend API tests separately cover cookie attributes, CSRF, fixation, rate limiting,
  redaction and role denial.
- Separate qualification: 30 GB dataset, distributed/multi-host execution, LDAP, AD, LFS, wiki, attachments, broader permissions, and full roundtrip.

Mocks do not replace Gitea integration evidence.

## Connection profile evidence (Issue #8)

Local run `68707345dbc14eeb` passed against the unchanged pinned Gitea 1.27.2 instances
on a Darwin/arm64 host with Linux/arm64 Docker. The report's `connection_profiles` section
requires thirteen assertions: real version/account, no implicit approval, explicit local approval,
read-only checks, unknown/wrong CA rejection, hostname mismatch, expired certificate rejection,
no token values in profile state/exports, authenticated API check/approval, actual CLI-process
restart with preserved approval, API/CLI/log/argument secret scans, and the default trust-store
path without a profile CA reference. Its TLS boundary is a generated localhost HTTPS
proxy forwarding to the isolated real target. This is not a native Gitea TLS deployment claim.
The default trust-store fixture uses a synthetic CA through local `SSL_CERT_FILE`; it is not a
live external public-CA endpoint test. Cleanup passed for that run's resources.

`make test` passed with 229 backend tests, Ruff, formatting, MyPy, TypeScript and frontend build.
The new tests cover real-process persistence, atomic import rollback, concurrent endpoint claims,
revision conflicts, schema-5 upgrades, API/CLI parity, reference safety, explicit trust and changed
account/certificate evidence, stale evidence approval rejection, a shared absolute deadline,
slow-drip cancellation, and encoded response rejection before decompression. Mandatory profile
report assertions have explicit missing/false mutation tests.
`make test-browser` passed nine scenarios including profile create/reload, stale-edit rejection,
replacement/retirement, successful import, malformed import rejection and keyboard controls,
alongside the existing authentication scenarios. Certificate evidence rendering uses a clearly
mocked probe response; the dialog must show both fingerprints before accepting a decision.
Hosted CI run `44840084900` passed for initial commit `0f7c92a`; current correction review and CI
are tracked on [PR #49](https://github.com/hackepeter87/GitFerry/pull/49). Evidence for the initial
commit does not approve a later correction or merge commit.

## Issue #9: plan-core work in progress

The branch for Issue #9 introduces local plan-state and runner tests. The current local quality
gate passed 244 backend tests, lint/format/type checks and the frontend build. New tests cover
plan publication rollback, concurrent publication, actual child-process reload, unchanged
original baselines after observation, stale profile rejection and safe cancellation. A child
process exits after a simulated remote acceptance but before journal confirmation; a fresh
process recovers `outcome_unknown` and does not increment the remote counter again, including
under a new plan round. These simulated remote tests do not establish the complete Gitea plan
workflow. API/CLI/UI integration and real-Gitea plan-specific interruption probes remain open.

Existing Gitea regression run `f61b3ad56bea4134` passed on the unchanged image pin and reported
successful run-scoped cleanup after the schema/adapter/structure-preflight changes. This is
regression evidence, not acceptance of Issue #9. See [ADR 0012](decisions/0012-durable-transfer-plans.md).

## Environment

The documented integration entry point is `make test-integration` on both local development and
Linux CI. Compose uses
`docker.gitea.com/gitea:1.27.2@sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`
for both instances. Ports bind to loopback only. Every run receives a unique `gitferry-it-*`
Compose project, project-scoped volumes and networks, and a run-scoped GitFerry runtime image. No
Docker socket or macOS host path is encoded in Compose.

The runner performs dependency/build work before the isolated transfer, runs a focused existing
v1/v2/comment state-contract gate, starts fresh volumes, and then executes the existing file-only
roundtrips. GitFerry A is attached only to the source internal network and GitFerry B only to the
target internal network. Each actual importer context must fail probes to the opposite Gitea and a
public address. The importer state volumes are separate and only transport directories are copied.

The entire Python probe is bounded (900 seconds by default); individual harness commands are also
bounded. Override only through `GITFERRY_IT_PROBE_TIMEOUT_SECONDS`, run-specific port variables, or
an explicit safe `GITFERRY_IT_RUN_ID`. A missing report, missing required section, false required
assertion, or failed cleanup makes the entry point fail.

Run `make test-browser` after installing the pinned Playwright Chromium runtime with
`cd frontend && npx playwright install chromium`. The test creates a unique temporary root, starts
separate bootstrapped and unbootstrapped FastAPI/SQLite processes, waits on HTTP readiness, and
removes only that validated temporary root. GitHub Actions installs Chromium with its Linux system
dependencies, runs the same Make target with a five-minute step limit, and uploads Playwright
traces, screenshots and videos when the browser step fails.

## Required fixtures

The seeder creates an organization, team, users with different access, selected and unselected repositories, multiple commits, a branch, annotated tag, label, issue, and comment. Target-only dummy records intentionally offset numeric IDs.

## Probe assertions

Git bundle: enumerate and compare ref names/object IDs; run `git bundle verify`; clone while the importer has no source or external network; compare reachable object sets; corrupt a copy and require verification failure.

Metadata: transfer issues/comments through documented REST endpoints; distinguish global IDs, repository issue indexes, comment IDs, native authors, origin information, timestamps, idempotent replay, and selected-field conflicts. Differences are evidence, not silently normalized success.

Permissions: copy one organization, read/write team configuration, and selected repository assignments; then clone/push as reader, writer, and denied functional users without administrator credentials. Compare the unselected control repository before and after.

OpenAPI/import: inspect each live `/swagger.v1.json` request and response schema, then separately exercise Gitea's repository migration endpoint. The repository-import result is not generalized to individual-object roundtrip.

Offline isolation: the importer is attached only to the internal target network, without a default external network; negative probes to source and a public IP must fail before import.

## Status vocabulary

- `passed`: executed assertion succeeded.
- `failed`: executed assertion contradicted expected behavior.
- `blocked`: environment or external prerequisite prevented execution.
- `not-implemented`: scenario has no implementation yet.

The latest concrete run is recorded by the integration report under `integration-results/` and summarized in the capability matrix. Generated reports are not committed by default.

## Capability claim gate

[`capability-matrix.md`](capability-matrix.md) is checked as a contract, not only reviewed as prose.
`backend/tests/test_capability_matrix_contract.py` requires the complete agreed capability ID set,
the documented status vocabulary, all workflow/status fields, one GitHub owner and target release
per row, resolvable local evidence links, the exact Gitea pin, and the known trust/timestamp/restart
limits. The existing transport tests separately pin the accepted Manifest v1 fixture bytes and
SHA-256 digests while validating Manifest v2 through its own schema.

A matrix claim is upgraded only when its cited evidence has run at the required layer. A source
module or unit test can establish implementation behavior but cannot replace the two-Gitea workflow
evidence required for `First import` or `Return`. When evidence conflicts or is unavailable, the row
is downgraded to `not-checked`, `blocked`, `not-implemented`, or a bounded status; the owning issue is
updated instead of treating the total number of passing tests as proof.

## Executed 0.1.0 foundation acceptance: 2026-11-20

The exact integrated-main commit `dedc0778677f1e88eaf1c0baebfee7a1ead0966d` passed hosted run
`44808991767`. Its `quality` job completed successfully, and its `gitea-integration` job used a clean
Linux/x86_64 checkout, Docker Linux/amd64 without observed emulation, and both pinned Gitea 1.27.2
images. All 19 report-contract checks passed. The deliberately failed inner run exited 86, retained
diagnostics, and both the run-local and outer cleanup passed.

A context-free resume probe first classified its unavailable GitHub network as blocked rather than
inferring success. On an explicitly authorized read-only retry it discovered the existing
`release/0.1.0-acceptance` branch, Issue #5 as the sole open milestone item, closed dependencies
#1–#4, the exact integrated check run and active ruleset, and selected the existing work without
creating a duplicate. Detailed local/hosted/configured/limited/unverified classifications are in
[`verification-0.1.0-foundation.md`](verification-0.1.0-foundation.md).

On the acceptance branch, `make doctor` passed on Darwin/arm64 with Docker Linux/arm64 29.9.3,
`make test` passed 144 backend tests plus Ruff/format/MyPy and the frontend build, and
`make test-clean` passed. The current acceptance branch did not rerun the full integration suite
locally; the exact integrated baseline received the fresh hosted Linux integration and controlled
failure runs described above.

The acceptance report then merged as `07fe353a896b83a29ee8013bddb80ef638e50c56`. Final main run
`44811562483` passed `quality` in 1m14s and `gitea-integration` in 2m46s. Artifact `14668890407`
binds the clean commit and exact Gitea pin, reports no failed or unexecuted required checks, and
records successful normal and outer cleanup. Its controlled failure exited 86, retained diagnostics,
and also completed both cleanup levels.

## Report, diagnostics, and cleanup contract

`integration-results/report.json` is the only machine-readable result contract. Its `execution`
section records the run ID, Git HEAD plus dirty/workspace digest, host and Docker platform,
architecture/emulation observation, configured and resolved Gitea images, required-evidence map,
failed or unexecuted checks, interruption transitions, and cleanup result. A successful probe is
not reported as passed until run-scoped cleanup succeeds.

On failure, `integration-results/diagnostics/` captures Compose status, image inventory, and
redacted service logs before cleanup. It does not capture Compose configuration, environments,
passwords, or tokens. The cleanup accepts only project names beginning `gitferry-it-`, removes
only resources labeled for that project plus its recorded run image, and never invokes a global
Docker prune.

`make test-integration-failure-contract` injects exit 86 after the healthy environment
synchronization point. The outer verifier requires a failed inner report, retained diagnostics,
successful cleanup, and zero remaining run-labeled containers, networks, volumes, or runtime
image. This is a test of failure handling; it is not a migrated operation.

GitHub Actions job `gitea-integration` uses the same entry point on `ubuntu-24.04`, a run/attempt
specific ID, a 55-minute job timeout with shorter bounded test/cleanup/upload steps, scoped cleanup before the always-executed result upload, and
immutable Node 24 action pins. A separate cleanup step cannot turn an earlier failed step green.
The report binds a hosted run to its GitHub run/attempt, source SHA, tested checkout SHA, ref, event,
workflow, job, clean Git checkout, platform, and resolved images. `workflow_dispatch` can inject the same deterministic
failure so the overall hosted run is expected to be red while cleanup and artifact publication
still execute. Its timeout probe uses a 30-second internal watchdog after environment startup; the
five-minute Actions step timeout is only a backstop. An independent `always()` step captures any
available run-scoped Compose diagnostics before outer cleanup, including when the primary report is
partial or missing.

## Executed feasibility run: 2026-11-20

`make test-integration` completed from fresh volumes against two Gitea 1.27.2 instances on Docker Desktop ARM64.

- Passed: branch names, tag name, annotated tag object ID, and reachable Git object IDs matched after an offline bundle import.
- Passed: importer could reach the target but not the source service or `1.1.1.1`.
- Passed: SHA-256 changed after corruption and a mirror clone rejected the corrupted pack.
- Observation: `git bundle verify` alone did not detect the changed pack footer; package integrity must not rely on that command alone.
- Passed: issue title, body, state, label, author login, comment body, comment author login, and issue/comment relationship matched.
- Limitation: ordinary issue creation did not preserve `created_at`.
- Limitation: the comment create request accepted but ignored the supplied source `created_at`; the target used its current time.
- Passed: `developer` mapped from source numeric ID 2 to target numeric ID 4 while preserving the login. Numeric IDs are mapping data, not identity.

The raw field-level report is `integration-results/report.json` for the local run and a CI artifact in GitHub Actions.

## Executed identity, permission, and roundtrip run: 2026-11-20

`make test-integration` completed from fresh volumes against two Gitea 1.27.2 instances. The
detailed inputs, mappings, expectations, observations, and limitations are in
[`verification-identities-permissions-roundtrip.md`](verification-identities-permissions-roundtrip.md).

- Passed: explicit realm/login/SID/OU mappings resolved users with different source/target IDs;
  missing email was accepted, while ambiguity, foreign binding, admin mapping, and no mapping
  failed closed in unit tests.
- Passed: read/write teams and selected repository assignments matched; reader clone succeeded,
  reader push failed, writer push succeeded, and an unmapped same-login user could not read.
- Passed: structure provisioning created no functional accounts; the technical owner remained
  separate, and a later explicit mapping added only the intended reader access.
- Passed: issue and comment native author IDs/logins matched distinct mapped accounts; updates by
  `gitferry-importer` did not replace those authors.
- Passed: existing issue return created no duplicate and retained `created_at`; A generated a new
  `updated_at` rather than restoring B's exact value.
- Passed: new B issue/comment creation on A retained content, mapped native authors, and relation;
  original timestamps were preserved only as origin information. Replay created no duplicates.
- Passed: drift in title/body/state/label-name baseline blocked an overwrite, while a control-repo
  change did not block the selected issue.
- Passed: live OpenAPI confirmed create requests expose neither historical creation time nor native
  author inputs.
- Passed with separately recorded product limitations: server-side repository migration preserved
  creation times but attributed issue and comment to the technical administrator, required live
  server-to-source access, and could not update the existing repository on replay.

## Executed durable-state and restart run: 2026-11-20

`make test-integration` completed from fresh Gitea volumes with the pinned 1.27.2 image. The state
database was file-backed; each apply/recovery step ran in a separate Python process.

- Passed: confirmed issue creation was recognized after restart and replay created no duplicate.
- Passed: a hard exit after the real Gitea POST but before local completion recovered as
  `outcome_unknown`; target ID remained unknown locally and replay did not POST.
- Passed: discarding a successful HTTP result produced the same persistent halt and no duplicate.
- Passed: a second process was blocked while the first held the local executor lock around a real
  Gitea operation.
- Passed: identity binding, object mapping, immutable baseline, desired state, last observation,
  selected drift decision, and conflict survived process restarts.
- Passed: control-repository change remained irrelevant; repeated reads did not replace the
  original baseline.
- Passed: schema/foreign-key/uniqueness/rollback tests, including a newer-schema refusal, ran in the
  backend suite.

Detailed status transitions, commands, transport boundary, and limitations are in
[`verification-durable-state-restart.md`](verification-durable-state-restart.md). A correctly
blocked unknown operation is a passing safety test but remains incomplete product work.

## Executed durable provisioning run: 2026-11-20

Run `f6765a2dec4a436a` completed from clean commit `2090642` on a Darwin/arm64 host with Linux/arm64 Docker
containers, using the pinned Gitea 1.27.2 image digest above. The selected structure path used the
reusable application service, a separate target-side SQLite state store, and a file bundle. The
mandatory report checks passed 18/18; run-scoped cleanup also passed.

- Passed: the service created one organization, one selected repository, and the planned
  `readers`/`writers` teams; Gitea also created its required `Owners` team. All nine bounded
  operations confirmed and a second-process replay created no new structure or changed refs.
- Passed: exact refs matched; the reader cloned but could not push, the writer cloned and pushed,
  and the outsider was denied. The control repository remained unchanged.
- Passed: no user accounts were created by transfer. The tested team map configured `repo.code`
  and `repo.issues`; live aggregate permission remains unclaimed beyond the explicitly mapped
  units.
- Passed: hard-exit-after-remote and lost-response probes each retained one executing event and
  `outcome_unknown`; replay sent no second POST, dependent work was `blocked_parent`, and a new
  package envelope could not bypass the halt.
- Passed: no credentials appeared in the SQLite state, plan, or report. The schema is v4.

The detailed input/mapping/expectation/evidence table is in
[`verification-durable-provisioning.md`](verification-durable-provisioning.md). This is local
evidence only; hosted CI has not run for the current branch.

## Executed portable transport roundtrip: 2026-11-20

`make test-integration` completed from fresh volumes against the pinned Gitea 1.27.2 image.
GitFerry A and B used separate SQLite named volumes. A was attached only to the source network and B
only to the target network; reciprocal HTTP reachability probes failed. The harness copied the
forward and return directories explicitly with Compose file-copy operations.

- Passed: forward manifest `portable-forward-1` was admitted on B, created one issue, and a new
  worker process replayed it without another POST.
- Passed: the return manifest updated the mapped A issue and created one B-origin issue on A.
- Passed: both target issues had native author `issue-author`; the B-born issue retained its B
  origin while receiving a new native A ID and native A creation time.
- Passed: relevant selected-field drift on A blocked before a Gitea write; a control-repository
  description change remained irrelevant and preserved.
- Passed: same-manifest replay after process restart and a new manifest envelope containing the
  same origins created no duplicates.
- Passed: unit tests rejected duplicate keys, an unknown version, byte-digest tampering, unsafe
  payload paths, wrong recipient, unknown forward reference, conflicting replicas/baselines, and
  imported approval or success markers.
- Limitation: transport-manifest v1 contains issues only. Historical native `created_at` is not
  restored, the SHA-256 is not a signature, and the result is not a general exactly-once claim.

Detailed facts and trust-boundary classification are in
[`verification-portable-transport-roundtrip.md`](verification-portable-transport-roundtrip.md).

## Executed Manifest v2 comment roundtrip: 2026-11-20

`make test-integration` completed from fresh volumes against the pinned Gitea 1.27.2 image, using
the same two independent GitFerry state volumes and isolated side-local networks.

- Passed: v1 remained accepted byte-for-byte while the version-selected v2 schema admitted normal
  issue comments with qualified authors and explicit stable parent issue origins.
- Passed: an A comment changed on B updated the same A comment ID and retained its parent, native
  author, and native creation time; no duplicate was created.
- Passed: a B comment on an A-origin issue kept B origin and A parent origin; a B issue/child pair
  listed child-first in the file executed issue-first and produced the correct native relation.
- Passed: process-restart replay and a new manifest envelope reused confirmed operations without
  extra comments or issues.
- Passed: selected A comment drift blocked before any return write; an unselected control-repo
  change did not block and remained intact.
- Passed: a hard exit after a real comment POST and a lost successful HTTP response both recovered
  as `outcome_unknown`; neither restart sent a second POST or invented a target ID.
- Live-contract limitation: `EditIssueCommentOption` contains only `body`, so the target membership
  and baseline read cannot be an atomic server-side precondition for PATCH.
- Scope limitation: normal comment create/body-update only; no deletes, parent moves, reviews,
  timelines, reactions, or attachments. Historical timestamps remain separate origin facts.

Detailed inputs, digests, observed IDs, authors, parents, timestamps, restart evidence, and limits
are in [`verification-manifest-v2-comments.md`](verification-manifest-v2-comments.md).

## Executed consolidated integration run: 2026-11-20

`make test-integration` passed locally on a Darwin/arm64 host with Docker Server Linux/arm64
29.9.3. Both actual Gitea images were Linux/arm64, resolved to image ID
`sha256:5b1bb1996a19a68ef3bcc669651500f37fb37206446cb777a3ced3b75f25a168`, and retained the
configured repository digest. No emulation was observed. The side-local GitFerry workers were
Linux containers; the orchestration host itself was not Linux.

The controlled failure contract also passed locally. No run-labeled container, network, volume,
or runtime image remained after either check.

GitHub-hosted final-head run `44794200527` then passed for PR #39 on Ubuntu 24.04/Linux x86_64.
`quality` passed in 1m12s and `gitea-integration` in 2m58s. The earlier inspected report from run
`44791010437` recorded Docker Linux/amd64 28.0.4 with no emulation and bound PR source commit
separately from tested merge checkout and Git HEAD. It recorded clean
checkout state, resolved both Gitea containers to image ID
`sha256:e8be8e1f776fe453b811d88887306b1d7bdf83577789465e5d186924014a57a6`, and passed the
metadata contract plus every existing mandatory assertion. Both uploaded reports include the
successful outer workflow cleanup. The controlled-failure report also records exit 86, diagnostics
captured, phase `environment-start`, and successful run-local cleanup.
This hosted run is distinct from both the earlier local Linux-container path and integrated-main
run `44794857027`, which passed merge commit
`24b147abb681cf066fdaf8b863e677065a1546fe` (`quality` 1m22s; integration 2m38s).

Two controlled top-level dispatches correctly remained failed. Run `44795325443` recorded exit 86
after environment startup; run `44795624522` recorded internal-watchdog exit 124 and phase
`controlled-timeout`. Both retained diagnostics, recorded successful run-local and outer cleanup,
and uploaded the failed report. Active ruleset `27751371` requires the exact `quality` and
`gitea-integration` contexts on current code and prevents bypass, deletion, and force push.
Exact run facts and the requirement-to-test map are in
[`verification-linux-integration-run.md`](verification-linux-integration-run.md) and the generated
`integration-results/report.json`.
