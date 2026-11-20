# Reproducible Linux integration-run verification

## Result and environment boundary

On 2026-11-20, `make test-integration` completed with run ID `d7430d29c274458b` from the dirty,
uncommitted workspace. The orchestration host was Darwin 27.2.0/arm64. Docker Server 29.9.3 and
all tested application/Gitea containers were Linux/arm64; no architecture emulation was observed.
This proves the Linux container execution path on the local Docker host; it is retained separately
from the hosted evidence below.

Both instances reported Gitea 1.27.2. Compose retained the configured image pin
`docker.gitea.com/gitea:1.27.2@sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`.
Docker resolved both running containers to Linux/arm64 image ID
`sha256:5b1bb1996a19a68ef3bcc669651500f37fb37206446cb777a3ced3b75f25a168`
and reported the same repository digest. The report records Git HEAD, the dirty flag, and a SHA-256
over the tested workspace inputs so this result is not confused with the unchanged commit alone.

## GitHub-hosted Linux evidence

GitHub Actions run `44791010437` executed PR #39 on `ubuntu-24.04`. The `quality` job passed in
1m22s and `gitea-integration` passed in 2m45s. The integration report records Linux/x86_64 host,
Linux/amd64 Docker Server 28.0.4, Python 3.13.9, runner architecture X64, and no emulation. Both
Gitea services reported version 1.27.2 and retained the configured digest pin. Docker resolved both
to image ID `sha256:e8be8e1f776fe453b811d88887306b1d7bdf83577789465e5d186924014a57a6`.

The run used GitHub run ID `44791010437`, attempt 1, event `pull_request`, and ref
`refs/pull/39/merge`. It records PR source commit
`b28915f967f0e4f599345bf8381635cae35e085e` separately from tested merge checkout and Git HEAD
`7cc8cce97b4b47f93f546635a659783f561844bf`. The checkout was clean. The report's
`run_metadata_contract` assertion passed, all mandatory evidence entries passed, and the uploaded
artifact was `gitferry-integration-results-44791010437-1` (14-day retention). Both normal and
controlled-failure reports record the successful outer workflow cleanup. Text inspection of
the uploaded report and logs found no credential, authorization header, token, password, API key,
or private-key value; the only matching report text describes locally retained authorization data.

## Unified execution contract

Local development and `.github/workflows/ci.yml` both invoke `make test-integration`. The runner:

1. creates a unique `gitferry-it-*` Compose project and run image;
2. executes the existing focused v1/v2/transport-state/comment unit gate;
3. builds before the isolated transfer and starts fresh project-scoped volumes;
4. uses two real pinned Gitea instances and separate GitFerry A/B SQLite volumes;
5. copies only explicit transport directories between sides;
6. proves from both actual GitFerry importer containers that the opposite Gitea and a public
   address are unreachable;
7. requires all existing probe sections and the requirement catalog to pass;
8. captures diagnostics before cleanup on failure;
9. removes only project-labeled resources and its recorded run image;
10. validates code/platform/image and hosted run identity, including source/checkout SHA semantics;
11. writes both run-local and outer workflow cleanup results before artifact publication.

The probe has a 900-second default bound and individual harness commands have bounded waits. It
does not encode a Docker socket path or a macOS-specific host path. Preparatory image/dependency
downloads can use the build network; the tested importer networks are `internal` and the transfer
requires no source-side or external network access.

## Requirement-to-evidence map

The generated report contains this map under `execution.required_checks`; missing entries are
failures, not implicit passes.

| Requirement | Concrete evidence |
|---|---|
| Manifest-v1 fixtures retain bytes/digests | `test_existing_v1_examples_validate_without_byte_or_digest_change` in the focused gate |
| Changed A comment updates the same A comment | `comment_transport_roundtrip.assertions.case_a_updated_same_comment` |
| New B comment attaches to original A issue | `case_b_b_origin_on_a_parent` |
| Child-first manifest executes new B issue first | `case_c_comment_before_issue_planned_parent_first` |
| Equal text, distinct origins remain distinct | `test_identical_text_comments_keep_distinct_origins_and_targets` |
| Wrong parent is rejected | `test_missing_or_differently_parented_target_is_not_recreated` |
| Missing author binding blocks | `test_missing_author_mapping_never_falls_back_to_importer` |
| Drift/stale baseline blocks overwrite | live `comment_drift_blocked_before_any_return_write` plus `test_stale_or_wrong_parent_update_conflicts_without_patch` |
| Replay/restart/new envelope/unknown do not duplicate | five live restart/replay assertions, all true |
| Control repository remains intact | `control_repository_change_preserved` |
| `blocked_parent` is unsent | `test_parent_block_survives_until_confirmed_mapping_and_no_early_post` verifies zero POST |
| Unknown resolution checks exact target facts | `test_unknown_comment_create_is_not_reposted_and_resolution_checks_parent` plus the live synchronization evidence |

All required report entries passed. Existing probes, rather than duplicate scenarios, supply the
evidence.

## Interruption evidence

For both the comment hard-exit and lost-response cases, the worker writes and fsyncs a test-only
synchronization record only after `GiteaCommentClient.create_comment` returns the accepted native
comment. A newly started worker reads that record and independently verifies the live object using
the expected target instance, repository, complete expected-parent comment listing, comment ID,
mapped native author, and exact body. The record's target ID is not passed into GitFerry recovery.

The durable operation therefore remains `outcome_unknown` with no locally confirmed target ID.
Counts before and after the restarted execution are identical, proving no second POST. The issue
interruption probe uses the same accepted-response synchronization pattern and verifies the live
issue after restart. `blocked_parent` remains a distinct focused unit proof: the child has zero
POSTs until a confirmed parent mapping exists.

These synchronization records make the failure point observable; elapsed sleeps are not used as
proof that the remote operation completed. They do not provide application reconciliation or an
exactly-once guarantee.

## Controlled failure and cleanup

`make test-integration-failure-contract` injected exit 86 immediately after all containers became
healthy. The inner run failed, retained Compose status/image/log diagnostics, reported cleanup as
passed, and the outer verifier found zero project-labeled containers, networks, volumes, or
run-specific image. Cleanup did not use global prune and preserved the original nonzero result.

The normal run also left zero run-labeled resources. Its report status is `passed`, cleanup exit is
0, and `failed_or_not_executed_checks` is empty.

## CI status and remaining limits

The `ubuntu-24.04` jobs use immutable Node 24 action commits, stable check contexts `quality` and
`gitea-integration`, bounded runtimes, run/attempt-specific names, scoped cleanup, and an
always-executed artifact upload. The positive PR run described above is actual hosted evidence.
The workflow also exposes an explicit `inject_integration_failure` dispatch input; the deliberately
red hosted dispatch and effective repository rules are recorded only after they have actually run
and been queried. Therefore:

- locally executed: unit/report checks, controlled Docker failure, full two-Gitea integration;
- Linux environment executed: Linux/arm64 Docker services and importer contexts on the local host;
- native Linux host executed: yes, GitHub-hosted Ubuntu/Linux x86_64;
- GitHub Actions workflow prepared: yes;
- GitHub Actions hosted runner executed: yes, positive PR run `44791010437`;
- deliberately failed hosted dispatch: pending until the workflow is integrated on `main`;
- repository required-check rules: pending until configured and queried.

Known product limits remain unchanged: no historical native timestamp restoration for ordinary
creates, no atomic server-side precondition between comment preflight and PATCH, no untested Gitea
permission dimensions, no general exactly-once claim, and no full transfer engine.
