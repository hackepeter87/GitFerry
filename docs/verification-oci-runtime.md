# Persistent OCI runtime verification

Date: 2026-11-20

Issue: [#6](https://github.com/hackepeter87/GitFerry/issues/6)

## Verified scope

The bounded runtime consists of the production Dockerfile, FastAPI lifespan initialization,
file-backed SQLite schema v4, persistent package/work directories, the reference Compose file, and
the existing operation journal. It does not include administrative authentication, connection
profiles, a general background job engine, automated backup/restore, or a published image.

The reusable integration probe starts the built image with `--network none`, a new named volume,
and no Docker socket or privileged mode. It seeds synthetic state through `StateStore`, stops and
removes the container, creates a different container against the same volume, and compares the
persisted facts exactly. It then sends SIGKILL, restarts, and verifies SQLite integrity and state a
second time.

## Local execution

The initial full local run `1f7bddd1bf014538` executed on a Darwin/arm64 host with a Linux/arm64
Docker 29.9.3 server and no observed emulation. Both real Gitea instances remained pinned to
1.27.2 and digest
`sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`.
The run was intentionally recorded as a dirty working-tree run while Issue #6 was being assembled;
it is local engineering evidence, not final hosted acceptance for a commit.

The report contained 19 mandatory checks and passed all of them. Its OCI section used image ID
`sha256:1cebea6ee235973e3261d9f30f7864f26ecc16e570780c8541619cea27966aba` and passed 21 assertions:

- non-root UID/GID, version/revision/license labels, healthcheck, and SIGTERM stop signal;
- fresh private directories and schema initialization;
- identity binding, object mapping, baseline, `outcome_unknown`, operation events, conflict, package
  marker, and work marker unchanged after container replacement;
- graceful shutdown observed in Uvicorn logs;
- SQLite integrity and the same durable state after forced process termination;
- corrupt database, schema version 999, read-only package directory, and symlinked package path all
  stopped startup with exit code 3;
- the corrupt and newer-schema database contents remained unchanged, and an unrelated synthetic
  secret value did not appear in the captured startup logs.

The reference Compose file was separately started with synthetic secret files and an empty CA
mount. `/readyz` returned managed schema version 4. Effective inspection confirmed UID/GID
`10001:10001`, a read-only root filesystem, all capabilities dropped, and no privileged mode. The
scoped deployment was then removed with its volume.

Executed gates:

- `make test`: 167 backend tests plus Ruff, formatting, MyPy, and the Vue production build;
- `make test-integration`: passed, including the 21 OCI assertions and the existing Gitea probes;
- `make test-integration-failure-contract`: passed with the injected failure retained;
- `make test-clean`: passed; no run-scoped container or volume remained.

The final clean local run `7d6bda77d2b24d06` was bound to source commit
`3f34768690056acab15896a4762a509bc7f6100f`. It passed all 19 required checks and all 21 OCI
assertions on the same Darwin/arm64 host and Linux/arm64 Docker server. Its report recorded an empty
`failed_or_not_executed_checks` list and successful run-scoped cleanup.

## Hosted evidence

GitHub Actions run
[`44821485414`](https://github.com/hackepeter87/GitFerry/actions/runs/44821485414) verified pull
request #45 with source commit `3f34768690056acab15896a4762a509bc7f6100f`. GitHub checked out
the generated pull-request merge commit `54a8c51df7d1769578dd7b5f1f6d5d471b416e1a`; the report
records both identities so that merge-ref evidence is not mistaken for evidence against another
source revision.

The hosted environment was Linux x86_64 on an X64 GitHub-hosted runner with Docker 28.0.4. The
Gitea containers used Linux/amd64 images without observed emulation and retained the pinned Gitea
1.27.2 digest. The `quality` job passed in 1m11s and `gitea-integration` passed in 3m12s. The
integration report recorded:

- all 20 hosted mandatory checks as `passed` and no failed or unexecuted checks;
- all 21 OCI runtime assertions as true;
- successful cleanup limited to Compose project `gitferry-it-gha-44821485414-1`;
- successful final workflow-scoped cleanup.

The same job also executed the controlled failure contract. Its deliberately failed probe returned
exit code 86, preserved the original failure, captured diagnostics, marked the unexecuted mandatory
checks as failed, and completed both run-scoped and final workflow cleanup successfully. The
published artifact `gitferry-integration-results-44821485414-1` contains both reports and their
diagnostics. This is hosted CI evidence, not a released or published OCI image.

## Remaining limits

- The durable operation journal proves that server-side state does not depend on a browser, but the
  general queued/background execution API and UI are owned by Issue #9.
- Secret and CA file references are mounted safely; live connection-profile consumption is owned by
  Issue #8.
- Filesystem-copy backup is only documented for a stopped service. Automated backup, restore, and
  upgrade qualification remains 0.6.0 scope.
- This is a single-host, single-active-writer SQLite boundary and not a distributed execution or
  cross-system exactly-once guarantee.
- No image was pushed or published.
