# Testing strategy and execution

## Layers

- Unit: blueprint/schema rules, realm/SID/OU identity ambiguity and collisions, SQLite migrations/constraints, operation transitions, selected issue baselines, manifest/path validation, and drift decisions.
- API: FastAPI request/response behavior with no external service.
- Integration: two real Gitea 1.27.2 containers, synthetic seed data, Git bundle and metadata probes, plus file-backed SQLite workers restarted or killed as separate operating-system processes.
- Portable transport integration: two additional GitFerry containers with independent named
  SQLite volumes and disjoint internal networks; the harness copies only immutable transport files.
- Later E2E: guided UI flows after the UI is implemented.
- Separate qualification: 30 GB dataset, distributed/multi-host execution, LDAP, AD, LFS, wiki, attachments, broader permissions, and full roundtrip.

Mocks do not replace Gitea integration evidence.

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
still execute.

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

GitHub-hosted run `44791010437` then passed for PR #39 on Ubuntu 24.04/Linux x86_64 with Docker
Linux/amd64 28.0.4 and no emulation. `quality` passed in 1m22s and `gitea-integration` in 2m45s.
The report bound PR source commit `b28915f967f0e4f599345bf8381635cae35e085e` separately from
tested merge checkout and Git HEAD `7cc8cce97b4b47f93f546635a659783f561844bf`, recorded clean
checkout state, resolved both Gitea containers to image ID
`sha256:e8be8e1f776fe453b811d88887306b1d7bdf83577789465e5d186924014a57a6`, and passed the
metadata contract plus every existing mandatory assertion. Both uploaded reports include the
successful outer workflow cleanup. The controlled-failure report also records exit 86, diagnostics
captured, phase `environment-start`, and successful run-local cleanup.
This hosted run is distinct from both the earlier local Linux-container path and the post-merge
`main` check still required for Issue #2. Exact run facts and the requirement-to-test map are in
[`verification-linux-integration-run.md`](verification-linux-integration-run.md) and the generated
`integration-results/report.json`.
