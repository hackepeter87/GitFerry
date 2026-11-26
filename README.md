# GitFerry

GitFerry is an open-source, platform-independent administration tool for controlled file-mediated
Gitea-to-Gitea repository transport, including selected Gitea metadata and explicit identity
mappings. The `0.3.0rc1` candidate provides the bounded desktop first-import and explicit directory-mapping workflows. It is not
a stable release or a complete implementation of later roadmap capabilities. The delivery plan is tracked in GitHub and the
[release roadmap](docs/roadmap.md).

## Current scope

- FastAPI backend, CLI and authenticated Vue 3/TypeScript administration frontend
- durable source export/download, package intake, explicit local identity decisions and first-import plans
- versioned instance and transfer blueprint schemas with secret references
- two-instance Gitea integration environment; the current reproducible primary baseline is Gitea
  28.4.0 at OCI index digest `sha256:aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309`.
  GitFerry itself is not locked to that exact release: it validates stable observed Gitea versions
  and records version-specific API evidence. Gitea 1.27.2 remains historical compatibility evidence.
- Git bundle, explicit identity/team permission, metadata roundtrip, and Gitea import probes
- versioned SQLite state for verified bindings, origins/mappings, selected baselines, bounded issue
  and normal-comment operations, conflicts, and controlled restart
- portable issue transport-manifest v1 with exact-byte SHA-256, transactional admission, CLI, and
  an isolated two-database A → B → A file-roundtrip proof
- compatible transport-manifest v2 support for normal issue comments, explicit stable parents,
  mapped native authors, body changes, and controlled restart/reconciliation
- architecture, ADRs, test evidence, capability matrix, and GitHub issue-first delivery process

Not yet implemented: a general transfer engine, production LDAP provisioning, a complete UI, productive-data migration, or GitLab support.

## Quick start

For an OCI installation, administrator bootstrap and mounted credential prerequisites, see
[the deployment guide](deploy/README.md), then [guided first import](docs/first-import.md).
The commands below prepare the development/test environment.

Prerequisites: Git, Python 3.11+ (the declared and tested minimum), Node.js 24+, Docker Desktop/Engine with Compose v2+, and enough local disk space for the test volumes.

```bash
make doctor
make setup
make test
make test-integration
make test-clean
```

If the default `python3` is older, select an installed supported interpreter with
`make setup PYTHON_BOOTSTRAP=/path/to/python3.11`. Setup rejects unsupported interpreters
before modifying `.venv`. OCI and the primary CI use Python 3.13.9; the quality gate also
installs and tests the backend on Python 3.11. See [the Python contract](docs/decisions/0020-python-support-contract.md).

The integration environment binds source and target HTTP ports to `127.0.0.1` only. All identities and credentials are synthetic and test-only.

## Commands

| Command | Purpose |
|---|---|
| `make doctor` | Verify architecture, Docker context, tools, and free space |
| `make setup` | Install pinned backend and frontend dependencies |
| `make test` | Run lint, type checks, unit/API tests, and frontend build |
| `make test-integration` | Run the report-gated, isolated two-Gitea Linux-container integration suite |
| `make test-integration-failure-contract` | Prove a controlled failure preserves diagnostics and cleans only its run |
| `make test-clean` | Remove only GitFerry-owned integration resources |

The bounded transport CLI uses the configured local SQLite path and never performs a Gitea write
during verification or admission:

```bash
gitferry transport create --database state.sqlite3 --local-instance instance-a \
  --input draft-manifest.json --output transport-directory
gitferry transport verify transport-directory
gitferry transport admit --database state.sqlite3 --local-instance instance-b \
  transport-directory
```

## Architecture and evidence

- [Architecture](docs/architecture.md)
- [Requirements and assumptions](docs/requirements.md)
- [Test strategy and execution](docs/testing.md)
- [Gitea capability matrix](docs/capability-matrix.md)
- [Identity, permission, and roundtrip evidence](docs/verification-identities-permissions-roundtrip.md)
- [Durable state and restart evidence](docs/verification-durable-state-restart.md)
- [Portable transport roundtrip evidence](docs/verification-portable-transport-roundtrip.md)
- [Manifest v2 comment evidence](docs/verification-manifest-v2-comments.md)
- [Reproducible Linux integration-run evidence](docs/verification-linux-integration-run.md)
- [Roadmap](docs/roadmap.md)
- [Delivery and release evidence](docs/release.md)
- [Verified upstream references](docs/references.md)
- [Architecture decisions](docs/decisions/)

Gitea REST patterns are based on the tested instance's official API contract. The integration suite
captures the live OpenAPI document at runtime rather than assuming a single Gitea release.

## License

Apache License 2.0. See [LICENSE](LICENSE).
