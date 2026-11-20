# GitFerry

GitFerry is an open-source, platform-independent tool for controlled Gitea-to-Gitea repository transport, including selected Gitea metadata and identity mappings. Iteration 1 is a verified foundation and feasibility study, not a complete migration product.

## Current scope

- FastAPI backend and Vue 3/TypeScript frontend skeletons
- versioned instance and transfer blueprint schemas with secret references
- two-instance Gitea 1.27.2 integration environment
- Git bundle, explicit identity/team permission, metadata roundtrip, and Gitea import probes
- versioned SQLite state for verified bindings, origins/mappings, selected baselines, bounded issue
  and normal-comment operations, conflicts, and controlled restart
- portable issue transport-manifest v1 with exact-byte SHA-256, transactional admission, CLI, and
  an isolated two-database A → B → A file-roundtrip proof
- compatible transport-manifest v2 support for normal issue comments, explicit stable parents,
  mapped native authors, body changes, and controlled restart/reconciliation
- architecture, ADRs, test evidence, and capability matrix

Not yet implemented: a general transfer engine, production LDAP provisioning, a complete UI, productive-data migration, or GitLab support.

## Quick start

Prerequisites: Git, Python 3.11+, Node.js 24+, Docker Desktop/Engine with Compose v2+, and enough local disk space for the test volumes.

```bash
make doctor
make setup
make test
make test-integration
make test-clean
```

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
- [Verified upstream references](docs/references.md)
- [Architecture decisions](docs/decisions/)

Gitea REST patterns are based on the official 1.27 API documentation. The integration suite also captures the tested instance OpenAPI document at runtime rather than assuming the API surface.

## License

Apache License 2.0. See [LICENSE](LICENSE).
