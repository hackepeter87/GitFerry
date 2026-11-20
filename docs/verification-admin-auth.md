# Administrator authentication verification

## Locally executed on this branch

The following commands were run from the repository checkout:

```text
make doctor
# passed: Darwin/arm64 host, Docker Desktop Linux/arm64 29.9.3
```

```text
make test
# passed: Ruff, format, MyPy, 190 backend tests, Vue typecheck/build
```

```text
make test-browser
# passed: 5 Playwright Chromium tests
```

```text
make container-build
# passed: image sha256:1c2e77cdb433027db724ab88d5f8e5475e79b199d16f16b7ba478979e98c9a4d
```

```text
make test-integration
# passed: run 9831417d4f324e21, Gitea 1.27.2 on both sides
make test-integration-failure-contract
# passed: injected failure remained failed, diagnostics and scoped cleanup retained
make test-clean
# passed
```

The unit/API/CLI tests cover one-time bootstrap, protected password-file handling, PBKDF2
persistence without the plaintext password, recovery and session revocation, durable expiry, CSRF,
cookie attributes, rate limiting, public health/readiness, minimal status, fail-closed API behavior,
role denial and schema migration. The Playwright suite uses synthetic identities and drove
bootstrap guidance, keyboard login, denied unauthenticated API access, logout, deterministic
server-side expiry, recovery revocation, refocus behavior and dark-mode field contrast through real
FastAPI processes and file-backed SQLite stores. ASGI request-log tests verify that only the route
template, method and status are logged; credentials, usernames and unmatched secret-like paths are
absent.

The full integration run used fresh run-scoped resources and the pinned Gitea image
`docker.gitea.com/gitea:1.27.2@sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd`.
Its OCI probe passed all 22 assertions, including `root_html`, the compiled JavaScript asset, the
minimal bootstrap API, container replacement, forced restart and fail-closed storage cases. The
report recorded no failed or unexecuted mandatory check and successful run-scoped cleanup.

This local integration report is bound to dirty worktree head
`316e477d6261b31eb8c1917b6b2bd57ead82d13a`; it is engineering evidence for the assembled branch,
not clean-commit or hosted evidence. The exact branch head must still pass Hosted CI after push.

`npm audit --audit-level=high` also completed against the registry with zero reported
vulnerabilities. No Python advisory scanner was added or claimed by this issue.

## Hosted CI

Hosted CI has not been executed on this branch. No hosted run ID, result, Gitea version, or image
digest is asserted by this document. A future hosted run must record its actual commit, checks, and
integration evidence before this branch can be treated as hosted-verified.
