# Testing strategy and execution

## Layers

- Unit: blueprint/schema rules, realm/SID/OU identity ambiguity and collisions, SQLite migrations/constraints, operation transitions, selected issue baselines, manifest/path validation, and drift decisions.
- API: FastAPI request/response behavior with no external service.
- Integration: two real Gitea containers, synthetic seed data, Git bundle and metadata probes, plus
  file-backed SQLite workers restarted or killed as separate operating-system processes. The current
  reproducible primary baseline is Gitea 28.4.0 at OCI index digest
  `sha256:aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309`; historical 1.27.2
  runs remain dated evidence.
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

## Issue #19: portable metadata and binary payloads

The metadata application-plan probe uses the pinned Gitea 28.4.0 baseline and synthetic
source/target accounts. It transfers a repository label and issue-label relation, an issue
attachment, a release authored through an explicit binding, and a release asset. The probe
checks native target IDs, parent relationships, author selection, payload bytes and SHA-256
digests, then replays the same durable plan after a process restart without additional
objects. The unselected control repository is compared before and after execution.

The same probe exercises a hard process exit and a discarded successful HTTP response. After
restart, each affected operation is `outcome_unknown`, the remote object is verified by a
separate read, and no second POST is issued. Explicit reconciliation rejects a wrong parent,
author or content candidate before confirmation. These are halted operations, not an
Exactly-once claim.

Package and API tests reject unsafe payload paths (absolute, traversal and symlink), external
download URLs, duplicate references, inaccessible files, unsupported sizes and digest/size
mismatches before any Gitea write. Payloads are copied only through the transport directory;
the importer does not access the exporter database or source Gitea network. Missing package
entries do not delete target metadata.

The bounded support contract is create/replay for labels, issue/comment attachments, releases
and release assets, plus same-ID label/release updates with immutable baselines. Issue-label
relations use stable issue and label origins and run only after their target mappings are
confirmed. Confirmed asset replay re-downloads the mapped target and verifies its size and
SHA-256. The real probe blocks same-name/tag collisions and stale target drift and proves that
omission does not delete target metadata. Native historical timestamps and attachment author fields are retained as
origin facts where the tested regular endpoints cannot restore them. Label-assignment
replacement, deletion, binary replacement, and a complete metadata return workflow remain
separately qualified work.
Manifest v1 and v2 fixtures continue to be verified by their original byte digests; v4 is
validated by its own schema and explicit parser branch.

## Python and planning contracts

Python 3.11 is the declared development minimum; the required quality job installs and runs
all backend tests there before its primary Python 3.13.9 gates. The OCI base remains pinned to
3.13.9. `make setup` rejects older interpreters before modifying the environment; select an
installed supported Python using `PYTHON_BOOTSTRAP` when necessary. Historical 3.9 runs are
not rewritten. [ADR-0020](decisions/0020-python-support-contract.md) explains the correction.

The [issue-contract audit](issue-contract.md) validates complete GitHub snapshots and fails on
missing/inconsistent metadata, evidence flags or truncated Project exports. Negative fixtures
run in `make test`. Scope adequacy and dependency direction still require independent review;
a passing structural audit is not a migration or milestone acceptance result.

## First-import delivery checks

The common `make test-integration` entry runs the packaged source and target applications in
their isolated Gitea networks. `frontend/integration/first-import.spec.ts` selects two source
repositories while excluding a control, creates a durable export, downloads its archive, uploads those exact bytes to the
target, makes explicit local identity decisions and requests a prerequisite preview before
execution. The core controls use bounded Tab navigation and visible-focus checks; file inputs
receive synthetic files through the browser test driver. The desktop viewports (1024×768 and 1440×900) also check the actual plan storage estimate,
overflow and accessibility. Mobile qualification is not required by the owner; desktop keyboard
operation, visible focus and readable errors remain mandatory. The target is then inspected for Git refs, native authors and the comment parent.
The browser and application process are restarted before observing the same confirmed plan and
checking replay for additional writes or objects. Source/target state remains separate.

`backend/tests/test_plan_previews.py` covers schema migration, competing requests/executors,
authority changes, closed diagnostics, CSRF, exact-generation queueing and an actual subprocess
termination while a preview is checking. `frontend/e2e/plans.spec.ts` separately covers blocked
and stale preview presentation, keyboard start, reload and browser disconnection; simulated
remote writes in that suite are not real-Gitea evidence.

The report contract rejects a missing source-browser assertion just as it rejects missing
existing transport, interruption or cleanup evidence. Record the exact tested commit, run,
review and acceptance outcome in the delivery issue/PR and Actions artifacts. The historical
records below describe their original scopes; they do not approve later changed code or accept
the complete 0.2 milestone.

## Current primary qualification (Issue #68)

Local run `d2c6c4314b7b46bb` passed the report-gated integration suite from fresh scoped volumes
on 2026-11-26. Both real Gitea services reported 28.4.0 and resolved the configured OCI index pin
`sha256:aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309` to Linux/arm64 image
manifest `sha256:93f860dce19a3bb924361c167c93c7a2a93d74a5600060772c643cbf65de01f1`;
Docker Server was Linux/arm64 29.9.3 with no observed emulation. The run passed metadata, directory, permissions,
inventory, package, manifest, issue/comment, restart/unknown-outcome, and cleanup assertions.
This is local evidence, not a hosted GitHub Actions run and not a claim that GitFerry requires
exactly 28.4.0 at runtime.

The separate `run-gitea-upgrade-probe.sh` qualification then used the immutable historical
1.27.2 source image and the primary 28.4.0 target image. Both live `/version` responses matched,
the target accepted the documented repository migration request with HTTP 201, and the synthetic
source issue was present in the imported target repository. This proves that bounded direction and
operation only; it is not an in-place database upgrade, a downgrade claim, or support evidence for
every intermediate Gitea release.

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

## Issue #9: accepted bounded application-plan execution

The plan core now connects to the fixed Gitea adapter and protected API/local CLI. Local tests
cover immutable plans, atomic publication, original baseline preservation, writer exclusion,
real child-process recovery with a simulated remote, exact author bindings and parent ordering.
A managed-worker test closes the HTTP client while a simulated write is in progress. The
Chromium suite now adds a real-backend browser test: queue with the keyboard, wait for the
simulated remote-write synchronization point, close the page, release the write, reopen and
observe the same confirmed journal operation. It also prepares a new draft through the UI
without changing that operation identity. All 14 browser tests passed; mocked API cases cover
review/queue/cancel, empty state, unknown-parent distinctions and delayed-response selection.
The browser remote is simulated; the independent Gitea probe below supplies remote evidence.

Local integration run `e5b4cbd772c74829` passed all 21 required report checks. Its new
`application_plans` section passed 14 assertions in the target-only Linux/arm64 container:
organization/team/repository plus Git-bundle/issue/comment execution; distinct native authors and
parent mapping; confirmed replay after process restart; real accepted comment POST followed by
hard exit before local confirmation; real accepted POST with its HTTP response discarded;
restart and new-packaging refusal without another POST; unchanged control repository; and
absence of token values in state and diagnostic evidence. Interrupted operations remain
`outcome_unknown`, and those plans remain blocked—not successfully imported.

The host was Darwin/arm64, Docker Server Linux/arm64 29.9.3, with no observed emulation. Both
Gitea instances retained pinned version 1.27.2 and the existing image digest. Run-scoped cleanup
passed. The first attempt correctly failed because the new test TLS proxy omitted the HTTP
authentication challenge for Git; diagnostic artifacts and cleanup worked. The proxy now
forwards that challenge. Existing manifest-v1/v2 and separate-state roundtrip regressions passed.

The initial local quality gate passed 270 backend tests plus lint, formatting, MyPy and frontend
build. The controlled failure contract and `make test-clean` also passed. Hosted run `44847979869` passed the backend/adapter source commit `79f25db` on Linux x86_64
with Docker Linux/amd64 28.0.4 and no emulation. Its tested PR merge checkout was `0f069e6`;
the report records both identities, all 22 mandatory checks including workflow cleanup, and
all 14 application-plan assertions. Both Gitea containers resolved to image ID
`sha256:e8be8e1f776fe453b811d88887306b1d7bdf83577789465e5d186924014a57a6`.
The controlled failure report correctly remains failed with exit 86 and successful cleanup.
Hosted run `44849763511` also passed the UI source commit `380e33c`.

Independent review then required categorized preflight diagnostics and real explicit-resolution
evidence. Local follow-up run `a0307e5e64414afc` passed all 21 required checks and 16 application
assertions on the same Linux/arm64 environment. Wrong-parent, wrong-native-author and wrong-body
comment candidates were rejected while the operation stayed unknown. Only after the restart and
new-packaging halt checks did an explicit operator-selected ID resolve the accepted comment;
resolution and another process restart confirmed the same target without any additional POST.
The lost-response case remains blocked. This is controlled reconciliation, not automatic identity
inference. The report records a dirty workspace at parent `380e33c`; final-commit hosted evidence
is required separately. The follow-up quality gate passed 281 backend tests, lint, formatting,
MyPy and frontend build. Closed diagnostic categories are tested for secret redaction.
Final exact-head AI review accepted source `f28f593`; hosted PR run `44850635166` passed.
PR #50 merged as `1b0bed567de73379d225cd0b1da99a9d6eb936bf`. Integrated-main run
`44851149501` passed both jobs. Its inspected report binds that clean merge commit, all
22 required checks, 16 application assertions and successful cleanup on Linux/x86_64,
Docker Linux/amd64, without emulation. Issue #9 is closed. The bounded
selection probe is not proof of a complete inventory or guided source-to-target import wizard.
See [the operator boundary](transfer-plans.md) and [ADR 0012](decisions/0012-durable-transfer-plans.md).

## Issue #10: source selection slice

The shared source-catalog service reads paginated organizations and their repositories through
locally approved HTTPS profiles. The initial local `make test` passed 295 backend tests, lint, format,
MyPy and frontend build. All 18 browser tests passed, including four mocked source-selection
cases: cross-page/organization selection, failures/source changes, session expiry, and explicit
selection with an in-flight refresh guard. Browser mocks do not replace remote evidence.

Real Gitea integration run `1a1f7621fab547dd` passed all 21 required checks and the mandatory
`source_catalog_lists_real_organization_and_repository_without_writes` assertion. The existing
Linux/arm64, pinned Gitea 1.27.2 setup returned the actual organization/repository through the
approved TLS profile without additional writes. Cleanup passed. This local dirty-workspace run
is not immutable-head or hosted-CI evidence for the pending Issue #10 PR.

Review required capturing the same credentials/TLS context for check and catalog read.
The correction adds rotation, approval-revocation and escaped-secret tests. Its local quality
gate passed 298 tests; real integration run `38372e7558c34a45` again passed all 21 required
checks and 17 application assertions, with successful cleanup. Current-commit hosted evidence
remains a separate merge gate.

This slice is namespace browsing, not complete content inventory or a completed first import.
The UI keeps selection in the current browser view and says that export/import execution is
not yet available. Remaining package, identity, execution and complete browser acceptance stay
in Issue #10. See [the current interface contract](first-import.md).

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

## Issue #10 application package file boundary

`backend/tests/test_application_package.py` exercises the real schema/parser and archive service,
including unchanged v1/v2 example bytes, explicit issue-author relationships, large native IDs as
strings, transfer-direction/scope conflicts, forbidden imported approval/status fields, missing
or undeclared files and immutable descriptors. A separate Python process verifies received files
without any source database. `test_package_archive.py` covers traversal/aliases, links, duplicate
entries, compression, actual/header limits, bounded central-directory allocation, changed source
bytes, cleanup and concurrent no-overwrite publication. These are file-boundary tests, not a
claim that complete source inventory or the first-import UI has been accepted.

The existing `application_plan_probe.py` now builds `application-plan.zip`; `plan_worker.py`
receives it in the target-only Linux context and supplies the validated bundle to the existing
application plan. Mandatory assertion `application_package_bundle_received_and_used` binds the
received descriptor digest, opposite-system isolation and confirmed Git push. Structure/metadata
setup remains the existing synthetic plan fixture. The report-contract deletion test rejects a
report lacking this assertion; existing interruption, author, parent and control-object evidence
remains mandatory.

Development run `7aeba64a2bb940ba` passed all 21 local required checks and 18 application-plan
assertions, with cleanup exit 0, pinned Gitea 1.27.2, Darwin/arm64 host and Linux/arm64 Docker,
without observed emulation. This was a dirty-worktree development run based on `6014da9`, not an
immutable-head acceptance. Final local `make doctor` and `make test` passed (343 backend tests, Ruff,
format, MyPy and frontend build). The focused package/report tests also passed. The independent review reproduced and fixed an embedded
metadata object whose origin repository was outside the declared selection; a regression now
rejects it. Additional file-read checks reject a source replaced by a FIFO without blocking, on both
archive writing and receipt. Receiver-side symlink replacement is also rejected.
The matrix contract explicitly includes the new file-boundary row without removing any prior
capability requirement. The controlled-failure contract and scoped cleanup also passed.
Hosted exact-head and integrated-main evidence belongs to
[PR #52](https://github.com/hackepeter87/GitFerry/pull/52) and is required before accepting the slice.

## Issue #10 selected source capture

The source-export service, CLI and migration 8 are covered by
`test_source_exports.py`, `test_source_snapshot.py`, `test_source_export_cli.py`, and the existing
Git-bundle/transport/state regressions. The child-process test exits after file publication and
before the SQLite completion transaction, then recovers without a source connection. This is a
read-only capture interruption, distinct from a possibly executed remote POST and its persistent
`outcome_unknown` state.

The integration entry now requires `source_export_capture_and_offline_file_receipt`, backed by
all eleven assertions in `source_exports.assertions`. Every omitted assertion has a report-contract
negative test. The probe creates an actual source package in the source-only context, copies its
archive to the target-only context, and checks replay, authors/parent data, Git payload, byte digests,
network isolation and independent state. Target receipt is file validation, not metadata application.
Existing remote-write interruption and v1/v2 compatibility requirements remain mandatory.

Local development integration run `0474a5a1c0604986` passed all 22 mandatory checks,
including all eleven source-export assertions, against the unchanged Gitea 1.27.2 pin. The host
was Darwin/arm64 and the Docker runtime Linux/arm64; this is a local Linux-container proof,
not a hosted GitHub Actions run. Scoped cleanup exited 0. The package was 7,077 bytes and had
SHA-256 `766d8622f179d060c799b9ec3e16a6c5ddd629edc3cc29662c852f76b2bf7ac4`.
This working-tree run proves the stated selected capture/file receipt, not complete target
application, the UI wizard, or the later immutable PR/main commits. This slice subsequently passed independent review and hosted-main run `44862775964` on
`5c7d303425bd593dc89937bb3bc978a4a94c0199`, including all 23 hosted mandatory checks. The
source-capture evidence does not itself claim the later target-application extension.

The first two development integration attempts rejected source capture because the mapper required
the aggregate team `permission` to equal the unit permissions. A separate synthetic Gitea 1.27.2
API contract probe established the actual response: `permission: none` alongside
`units_map: {repo.code: read, repo.issues: read}` (and `owner` for the Owners team). The exporter
now uses equal supported code/issue units and excludes ownership. The failed runs retained
diagnostics and completed scoped cleanup. This correction preserves the existing effective-right
requirements; it does not weaken the assertion that teams must actually be captured.

## Issue #10 local package admission and plan preparation

`test_package_admission.py` exercises generated source packages received through an archive into
an independent database. It checks rolled-back review, atomic fact/receipt admission, schema-8
upgrade, exact-byte aliases, permanent origins/parents, explicit author prerequisites, structure
without business users, changed profile/selection conflicts, receipt replay in a new Python
process and preservation of an unknown operation under repackaging. It also replaces an embedded
manifest after initial inspection: both admission and selection must reject the newly read bytes
unless they match the descriptor digest. `test_package_api.py` covers the authenticated real
service path and sanitized conflicts; selection and CLI tests cover the thin application boundaries.

The existing source-export integration probe adds six mandatory assertions. The target imports
only the source-produced archive into its own state, applies the existing plan runner, checks
native issue/comment authors and their parent, compares transferred Git refs, then repeats receipt,
planning and execution in another process. The same durable plan must be recovered without extra
remote objects or mutation events. Missing bindings block metadata planning before any operation
is prepared. Target access to the source and external network is checked from the actual importer.
Each added assertion has a deletion-negative report-contract test; omission is not success.

During development, an attempted copy onto an already read-only mounted helper stopped the first
integration run. Removing that redundant copy preserved the mount. The subsequent actual import
and replay succeeded, but the relationship assertion compared different Gitea fields: the
comment's `issue_url` is the parent's web URL, whereas the issue's `url` is its API URL. Captured
native responses established this distinction; the assertion now compares `comment.issue_url`
with `issue.html_url`, in addition to reading the expected issue's comment list and checking native
author IDs. Failed runs retained diagnostics and completed scoped cleanup; they are not passing
integration evidence. Bounded comparison facts are retained in
`integration-results/work/package-admission-evidence.json` before cleanup.

The complete guided browser flow remains unaccepted. These tests do not establish LDAP/AD,
additional permission units, native historical timestamp restoration, atomic comment PATCH
preconditions, signed sender provenance, general exactly-once behavior or 30 GB operation.

Local integration run `0135345abcb141ce` passed all 22 required checks and all 17 source/package
assertions, including the actual target import and same-plan process restart. It used the unchanged
Gitea 1.27.2 image pin on a Darwin/arm64 host with Linux/arm64 Docker containers and no observed
emulation. Scoped cleanup exited 0; no required check failed or remained unexecuted. This is a
working-tree local Linux-container result, not an exact-commit hosted result. `make doctor` and
`make test` also passed (419 backend tests, Ruff/format, MyPy over 51 source files, frontend build).
Exact-head review, hosted CI and integrated-main verification remain required before integration.

The controlled failure contract also passed: its deliberately failed run retained diagnostics and
completed scoped cleanup. It is not counted as a successful migration.

The first PR #54 hosted run (`44865912507`) failed at the immediate branch API read after
target application. Preserved Gitea 1.27.2 logs show a nil-repository panic in `repo.GetBranch`;
the transferred bundle contains the expected `main` ref. The probe now waits on concrete
post-receive visibility: `repository.empty == false` and a successful branch GET, then compares
the returned commit with the bundle. The wait has a 20-second polling deadline and 2-second
request timeouts; only reads are retried. A persistent error or missing branch still fails.
Observations are retained in the comparison artifact. Focused tests prove delayed visibility and
timeout rejection. This addresses a suspected post-receive timing boundary without changing the
image pin, accepting a missing branch or retrying a remote write. PR run `44866454249` and
integrated-main run `44866950765` subsequently passed. Downloaded main evidence binds the clean
source and checkout to `9e6b2f4bb40329b2288d50be379607173ab55759`, with all 23 required checks,
17 source/package assertions, Linux/x86_64, Gitea 1.27.2 and cleanup exit 0.

## Issue #10 guided source commands and downloads

The source screen queues an immutable command through the protected HTTP API. The existing
background worker captures and packages it without an open browser. SQLite migration 10 stores
delivery state separately from the source capture and target operation journal. Module tests in
`test_source_export_jobs.py` exercise explicit queueing, shared executor exclusion, immutable
requests, schema-9 upgrade, file integrity, explicit retry and actual child-process recovery after
archive publication. `test_source_export_api.py` covers authentication, CSRF, worker availability
before persistence, wake-up and closing the verified download descriptor, including early exit.

`frontend/e2e/source-export.spec.ts` uses mocked API responses to verify exact selected IDs and
profile revision, keyboard activation, saved status/polling/download links, auth expiry, explicit
retry, and lost-response retry after reload using the same ID and request. It does not claim
browser-to-Gitea acceptance. Saved downloads remain accessible without reselecting or contacting
a source. The connection-export browser assertion now selects the exact configuration Export
button, because the page also offers source-export actions; its download and keyboard checks
remain intact.

The existing real source-export probe now runs the actual managed application lifespan, admin
API and background worker against the isolated Gitea source. It downloads the resulting archive
through the authenticated streaming endpoint; the independent target imports those files using
the existing probe. A new process repeats the command and downloads identical bytes while the
source TLS proxy is stopped. Two additional source assertions are mandatory:
`api_queue_background_download` and `api_restart_replay_no_source_access`. Each has a
missing-evidence negative report test. Existing capture/core replay, target-author/parent/Git,
network-isolation and unknown-write assertions remain required.

During development, run `8edff61f8f3c4137` failed before source capture confirmation. The probe
mixed a manually injected worker with the OCI image's enabled managed lifespan, which replaces
that worker. The corrected probe supplies disjoint persistent paths and local references to the
normal managed startup instead. The failed run retained diagnostics and completed cleanup (exit
0); it is not passing capability evidence. Source-worker failures now retain exception types and
code locations without exception messages or credentials.

The corrected local run `de63a7d49b504cb3` passed all 22 mandatory checks and all 19 source/package
assertions against the unchanged Gitea 1.27.2 pin. The host was Darwin/arm64; the Docker server and
test containers were Linux/arm64 with no observed emulation. Cleanup exited 0 and no mandatory
check failed or remained unexecuted. `make doctor`, `make test` (442 backend tests, Ruff/format,
MyPy over 53 source files, frontend build) and all 23 Chromium browser checks passed. A later
focused API check additionally covers stale-profile conflict classification. The controlled
failure contract retained diagnostics and cleaned successfully; `make test-clean` left no
GitFerry test containers or volumes. These are local working-tree results; exact-commit review,
hosted checks and integrated-main verification are required separately. The complete target
intake/identity-prerequisite browser flow and milestone 0.2 acceptance remain open.

## Durable target archive upload (Issue #10)

The prior source-export slice is integrated as PR #55, `de6fcf4`, with exact-main hosted
[CI run 44870014502](https://github.com/hackepeter87/GitFerry/actions/runs/44870014502) passing
23 required checks. That evidence is distinct from this target-upload working-tree evidence.

Local run `965a38877500458d` passed all 22 mandatory checks and 21 source/package assertions.
Both actual Gitea versions were 1.27.2 at the unchanged image digest recorded above; host
Darwin/arm64, Docker server and containers Linux/arm64, no observed emulation. Cleanup exited
0 and no required check was failed or unexecuted. The report identifies a dirty working tree
based on `de6fcf4`, not an exact clean commit or a hosted run.

The existing target-only worker now uses the normal managed application lifespan and
administrator-authenticated raw ZIP PUT/list/detail/resume API. It admits no origins, bindings,
receipts or operations during upload. Only after explicit local setup does it invoke the existing
admission/plan execution and validate Git, issue and comment authors/relationships. A separate
process uploads the same bytes/ID again and checks the same package digest, no additional
remote writes/objects and unchanged local admission counts. Existing network-isolation and
v1/v2 compatibility assertions remain required. Added mandatory assertions are
`target_upload_files_only_before_local_setup` and `target_upload_replay_persistent_identity`;
negative report-contract cases reject either missing assertion.

`make doctor` and `make test` passed (457 backend tests, Ruff/format, MyPy over 55 source files,
frontend type check/build). The subsequently added receive-timeout test passed with the other
12 upload tests. Those tests also cover actual process exits after archive/package publication,
new-process recovery, size/content conflicts, unsafe existing destinations and auth/CSRF.
All 29 Chromium browser cases passed, including six upload cases: raw request contract,
GET-only reload, explicit resume error, distinct IDs for subsequent files, lost-reply ID reuse
and stale-detail-response rejection. Browser response fixtures prove UI behavior; the managed
API integration separately supplies real-Gitea evidence. They do not yet prove the complete
Issue #10 first-import browser flow.

An initial controlled-failure attempt overlapped the normal integration run and failed to bind
its fixed localhost ports. It is not accepted failure-contract evidence; these two entry points
must run sequentially on one host. The failed report was retained before the sequential retry.
The sequential failure-contract run `failure-20261121053914-93206` passed: deliberate exit 86,
failed report, retained diagnostics, and run-scoped cleanup exit 0. `make test-clean` then
reported no remaining GitFerry integration projects. The final focused browser rerun also
checks keyboard submission and a 390-pixel ready-receipt layout without horizontal overflow.


## Guided local decisions and real target browser (Issue #10)

The current slice reuses the existing package admission, exact selection review, identity
bindings and background plan executor. `test_package_decisions.py` covers explicit registration,
qualified selectors, non-admin native account checks, IDs beyond JavaScript's safe integer,
changed authority, immutable binding conflicts, transaction rollback and admin/CSRF boundaries.
No database migration or transport-format change is needed.

Local gates on 2026-11-21 passed: `make doctor`, `make test` (474 backend tests, Ruff, format,
MyPy and production frontend build), the existing 32-case browser suite, and the subsequently
expanded seven-case intake suite. Intake cases additionally cover missing bindings, author
credential aliases, structure/Git-only exclusion, OU-distinct forms, stale responses and focus
transfer to the saved draft. Axe 4.14.0 scans the intake/review page at 1280 and 390 pixels with
no excluded rules; the narrow-width check also rejects horizontal overflow. Axe is a development
only dependency following the [official Playwright integration](https://playwright.dev/docs/accessibility-testing).
These scans do not replace manual keyboard or complete accessibility acceptance.

Real local integration run `2e007e9bfabe4733` passed all 22 local mandatory checks and all 23
`source_exports.assertions`. Environment: Darwin/arm64 host, Linux/arm64 Docker 29.9.3, no
observed emulation, Gitea 1.27.2 on both sides with the existing image digest unchanged.
The report records the dirty source snapshot hash; it is not an exact-commit hosted result.
The final focus/style adjustment is covered by the focused browser tests and still requires
exact-commit hosted execution with the final image.

`frontend/integration/first-import.spec.ts` uses the actual built frontend and managed backend,
without mocked API routes. The harness supplies synthetic bootstrap credentials, mounted token
references and a target-only TLS adapter, but no sender/realm/binding/receipt/plan facts. The
browser imports a non-secret connection profile through the normal configuration UI, explicitly
approves it, uploads the source-generated ZIP, confirms local decisions and starts its reviewed
plan. Native issue author 21 and comment author 20 match their distinct local target accounts;
the comment is attached to the imported issue and the Git ref matches the bundle. A new app
process and fresh browser observe the confirmed plan without mutation requests. A separate
replay verifies the same plan ID, no new objects and no additional remote write events.

A loopback-only test tunnel uses Docker exec to forward browser TCP streams exclusively to
`localhost:8080` inside `gitferry-b`. No importer network is added or made non-internal; the
actual target context still proves that source Gitea and external hosts are unreachable. There
is no fixed Docker socket or platform-specific host path. Each tunnel connection and subprocess
has bounded lifetime. Browser network traces are disabled for this real-session probe so session
cookies cannot enter publishable traces. Screenshots, assertion logs, sanitized server logs and
credential-free result summaries remain available in `integration-results/work/browser-*` and
`package-admission-evidence.json` before normal scoped cleanup.

Two preceding failed runs were retained as diagnostic evidence: `73d166d01ad643b0` detected that
a host-published port was unavailable on the internal Docker network; `4541035edaec49e3` detected
the missing accessible label on the configuration file input. Both ended failed and cleaned up
with exit code 0. The ingress tunnel and input label correct those causes; no assertion was
removed to obtain success. Cleanup of the successful run also returned 0.

This proves the bounded target intake flow, not full 0.2 acceptance: source-side complete browser
acceptance, pre-execution target collision presentation and remaining Issue #10 user-flow checks
stay open. No real LDAP/AD, 30 GB, historical native timestamp restoration, general exactly-once
or atomic comment precheck/PATCH claim follows from this run.

The sequential failure contract `failure-20261121063107-99769` passed: the injected failure kept exit
code 86, captured diagnostics and completed scoped cleanup with exit code 0.
`make test-clean` then completed successfully. Hosted verification of this slice remains pending.

Hosted PR run `44876141315` subsequently passed both jobs for source `785f6d3`, using clean
merge checkout `7117cf5`. Its success report has all 23 hosted mandatory checks; the deliberate
failure report retains exit 86 and diagnostics. Both cleanup results are 0. Independent review
then required stronger browser-upload identity linkage and stale target/package UI corrections.
Those corrections add exact observed upload-ID equality, clear checked account/credential/message
state on target changes, and preserve connection-profile refresh on package changes. The expanded
eight-case intake suite and frontend build passed locally after these changes; the earlier hosted
result must not be reused as approval for the corrected commit.

## Identity administration and directory qualification (0.3 development)

`test_identity_decisions.py` and `test_identity_application.py` exercise the existing binding
constraints through durable candidate decisions, actual subprocess restart and concurrent
approval, conflict savepoint rollback, native authentication-source changes, CSRF/local actor
validation and CLI reuse. `frontend/e2e/identity.spec.ts` covers explicit candidate submission,
reopening, retired connections, native decimal IDs and readable actor/reason history. These
route-mocked browser checks alone do not prove directory or real target access.

The common integration entry adds an isolated OpenLDAP fixture to the target network, using
the existing digest-pinned Python base and explicitly pinned OpenLDAP packages. The real
Gitea 1.27.2 probe checks LDAPS simple authentication, wrong password, wrong CA/recovery,
non-administrator access, same-name local collision, missing-mail native behavior and directory
DN/external-ID/group observations. LDAP results never qualify Active Directory SID or nested
group behavior. The standalone `tests/integration/directory/run.sh` is a focused diagnostic
entry; the common report and required evidence catalog remain the delivery gate.

The desktop first-import probe also executes structure/Git-only provisioning before any
functional binding, then explicitly approves identities and prepares the complete plan from
the same received package. It checks reuse of confirmed structure operations, native metadata
relationships, unchanged control selection and restart/replay. Technical organization
ownership remains explicit; “no user transfer” never means ownerless Gitea organizations.

The separate target-local bootstrap fixture uses the original static binary from the pinned
Gitea image with that server's configuration/volume; the image's `/usr/local/bin/gitea` wrapper
is not portable on its own. The fixture never grants those mounts to either importer. Actual
successful CLI creation is followed by a process exit or discarded return value; separate
worker processes must retain `outcome_unknown`, an unset confirmed native ID and one recorded
creation call per source. Native source presence is read through Gitea CLI, never supplied to
GitFerry as a recovery identity by the harness. Confirmed replay also preserves journal events.

Directory settings tests cover schema-discriminated simple/BindDN input, local profile revision
refresh, authenticated API/CSRF, configuration export/import, and inspection of uncertain
bootstrap state without server access. The desktop separates historical execution evidence
from a later settings revision. The report gate requires the LDAP negative cases and each
interruption create counter; missing facts fail closed.

Run evidence belongs to the associated issue/PR and Actions artifacts. These additional tests
are development work until the exact delivery commit passes the complete gates and review.

### Directory claim refresh

`test_directory_claims.py` checks bounded searches, filter escaping, ambiguous results, binary
SID normalization, group ordering and verified TLS client configuration. The real
`directory_claim_worker.py` uses the installed `ldap3==2.9.1` client against the isolated
OpenLDAP service. Its required report entry distinguishes equal logins in separate realms by
external ID and DN/OU, preserves missing email, and checks direct groups, real BindDN credentials,
wrong credentials and duplicate-search rejection. The synthetic CA includes strict key usages;
the application does not weaken certificate validation to accept the fixture.

`test_directory_identity_application.py` checks separate source/target claims, missing email,
configuration revision races, immutable bindings, native account/source correlation and durable
conflicts for changed external IDs, DN/OU and groups. An unavailable directory blocks execution
without inventing a conflict-free fallback. Restoring facts does not clear a recorded conflict.
The desktop tests additionally reject a check response that omits evidence for a selected
directory. These unit/browser contracts do not by themselves qualify real directory permissions.


### Separate Samba AD-compatible claim probe

The common runner also starts Samba 4.22.11 from explicitly pinned Debian packages on the
existing Python base. `ad_probe.py` records its actual version, image ID and platform. The
application reader observes real binary SIDs, DN/OU, missing mail and direct membership over
verified LDAPS. Enabled functional-user UPN/domain binds, a different CA, wrong password and
disabled-account exclusion are explicit assertions. A separate nested matching-rule query
qualifies the server response only; the product does not expand transitive groups. Searches
use an explicit OU base because referrals are intentionally rejected.

This is Samba AD-compatible evidence, not Windows Server AD, trust/replication, Kerberos client
qualification of every Gitea AD deployment. `tests/integration/ad/run.sh` is the focused diagnostic
entry; common report assertions and scoped cleanup remain mandatory for delivery.


### Directory realm and recovery qualification

The existing common integration entry also exercises two OpenLDAP bases with equal `uid` values,
different external IDs and DNs, explicitly selected native Gitea aliases and separate local realms.
The desktop configures and approves both sources; the existing late-mapping import then checks real
clone/push/deny and native author results. No new transport format or shared importer state is used.
The recovery worker stops the directory, checks fail-closed behavior, restores availability using a
bounded readiness check, mutates group and external-ID facts, and checks durable conflicts in fresh
processes. Desktop phase 6 explicitly reopens and approves only after original facts are restored.
The common report requires these assertions; absent evidence cannot pass.

Samba qualification independently checks actual Gitea login and source-linked alias identity,
including a directory account without mail and denied disabled/wrong-password attempts. Its public
CA is appended to the isolated target trust volume without replacing the OpenLDAP CA. Technical
administration remains separate; no database is edited. Field-level limits are in `directories.md`.


The target-local `ad_identity_probe.py` uses the same identity services and SQLite state as the
application. It explicitly approves a source-qualified claim against a real Samba SID and native
Gitea alias, without granting repository rights. Fresh worker processes check directory outage,
direct-group changes, disabled accounts, and explicit review after restored facts. Deleting and
recreating the synthetic LDAP object at the same DN/login obtains a server-assigned new SID;
GitFerry must retain the original binding and conflict even when Gitea still authenticates the
same native alias. Reopen cannot authorize that identity replacement. The common report requires
all recovery assertions and records the before/after SID, DN and local decision states. LDAP writes
are fixture administration over verified LDAPS, not direct database manipulation.

These checks qualify AD-compatible binding preflight and recovery. The complete desktop
provisioning/rights workflow remains separately OpenLDAP-qualified. A passed blocking test leaves
the changed-SID operation unresolved; it does not report that identity as migrated.


## Selected-content inventory and offline plan coverage

Issue #17 uses the existing entry points; there is no second integration runner.
`content_inventory_probe.py` seeds real Gitea 28.4.0 content and compares all 21 class counts
against independent fixture enumeration: 19 observable classes, with LFS and additional
unenumerated features explicitly unknown. It also qualifies native empty repositories, API
reaction null semantics, parent relationships, binary size facts and an unchanged unselected
control repository. Collection performs only GET requests.

The required report entry `selected_content_inventory_and_portable_plan` combines these facts
with the actual desktop inventory review, inventory-bound application-package export and
existing isolated file-first-import flow. Missing assertions or counts fail the report contract.
The TLS test proxy preserves Gitea pagination headers; dropping them must block collection.

Focused tests cover exact-page totals, duplicates, permission/rate/API failures, source drift,
immutable snapshots after real process restart, schema limits, auth/CSRF, API/CLI parity, and
package source/replica/author/parent consistency. Browser cases cover explicit review, option
changes, unsupported-class diagnosis, reload and lost-response replay. Product support is
limited to the current transfer adapters; observing wiki, LFS limitations, assets or reviews
does not establish their transfer. See [inventory operation](content-inventory.md) and
[ADR 0024](decisions/0024-selected-content-inventory.md). Exact run results remain in the issue,
PR and Actions artifacts.

## Pull requests, reviews, timelines and reactions

Issue #20 extends the same real-instance run; it does not introduce a second runner. The inventory
probe creates a PR, a normal PR conversation comment, a review with an inline comment, timeline
events and reactions. The OpenAPI probe records exact request fields, response fields and route
methods from both live Gitea 28.4.0 instances. The report contract requires the field-level
assertions and support classification, so a missing endpoint or silently skipped check fails the
integration gate.

The same fixture verifies captured head/base refs and SHAs, open and merged PR state, native merge
timestamps and commit, COMMENT and APPROVE review decisions, inline positions, native inline/reply
authors, timeline API ordering, and live resolve/unresolve/re-resolve transitions. Gitea 28.4.0
returns `merged_by: null` for the API-created merged fixture; the ordered timeline separately
retains the administrative merge actor. A live `content_version` PATCH succeeds with the current
value; reusing the stale value returns 409 without applying stale title/body fields. This is an
optimistic PR-content guard, not a complete GitFerry PR return adapter or a claim that Gitea wraps
every multi-field mutation in one transaction.

These classes are inventory-only. Export and plan review disclose the ref/author/position/timestamp
limits before execution; they do not enqueue target writes. Details and the separate
first-import/update/deletion classification are in
[the qualification report](verification-pr-review-metadata.md) and
[ADR 0028](decisions/0028-pull-review-metadata-boundary.md).

Before the guided target-collision and identity-recovery no-write snapshots, the synthetic harness
waits for Gitea's preceding fixture work with `gitea manager flush-queues --timeout 30s` (45-second
outer bound). A nonzero result fails the probe. The scoped diagnostic records are
`fixture-queues-collision.json` and `fixture-queues-identity-recovery.json`. This setup barrier keeps
an earlier functional Git push's asynchronous `updated_at` change outside the measured operation;
the full before/after snapshot, native timestamps, operation journal and no-write checks remain
unchanged. No fixed sleep or ignored timestamp substitutes for this boundary.

The inventory qualification also flushes source fixture queues before independent
enumeration: Gitea creates PR refs asynchronously. It retains per-class expected,
collected, and independently re-read counts in `inventory-counts.json`; any
difference fails the run. Queue completion is bounded and recorded, including
failure and timeout. This fixture boundary does not alter production drift rules.


## Native LFS and wiki first import (Issue #18)

The common `make test-integration` run includes `native_payload_probe.py` and the
existing production-image desktop first-import workflow. The latter selects LFS
and wiki content explicitly: two wiki histories and two distinct LFS objects,
including a pointer reachable only through an earlier revision. Target native
bytes, wiki refs and page content are checked independently after UI execution.
Manifest v1/v2 and application-package v1/v2 compatibility tests remain unchanged.

`native_payload_worker.py` uses independent source/target SQLite volumes and only
the copied archive. Its actual contexts test opposite-instance/public-network
isolation. Normal replay, process exit 88 after successful upload but before local
commit, and a proxy dropping an already-successful LFS PUT response run in separate
cases. Replay and new package IDs must retain the same operation identities and
produce no second upload. Explicit reconciliation verifies target bytes before
resuming the original plan. A passing interruption assertion leaves the operation
`outcome_unknown` until that explicit step; it is not a successful migration.

The negative worker tests unavailable pointers, missing/corrupt packaged objects,
and disabled/uninitialized source wikis. API/Git errors never establish empty wiki
history. Unit tests additionally cover path traversal, hardlinks/symlinks, byte and
count bounds, ref-name credential-word false positives, immutable changed intents,
and confirmation/reconciliation of 10,000 refs without oversized journal evidence.

The bounded return worker copies a new B LFS object and changed wiki bundle back to
A. A reads its original wiki comparison refs from its own export, verifies returned
Git-history pointers and LFS bytes, and rejects a subsequent ref-specific A drift.
This is native-adapter feasibility only: no application return plan, deletion,
branch conversion, wiki authorization transfer or general merge capability follows.
Exact commits, runs, failure diagnostics and final acceptance belong to #18/its PR
and the common Actions report rather than a separate evidence-only commit chain.
