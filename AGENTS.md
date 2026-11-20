# GitFerry agent rules

The project-owner requirements and `docs/requirements.md` define scope. Never claim a migration capability unless a real Gitea integration test supports it.

## Required workflow

- Use the installed, version-matched ProjectAtlas skill and MCP tools before broad source reads. Run `atlas_init` only when project-local state is absent, then use `atlas_watch_once` after edits.
- Keep the full transfer engine, production LDAP provisioning, full web UI, productive-data migration, and GitLab support out of iteration 1.
- Work in small testable slices. Do not weaken a test or expectation to make a run green.
- Never commit, push, tag, or open a pull request without explicit human approval.
- Treat blueprints, package archives, API data, paths, and filenames as untrusted input.
- Never put credentials, tokens, private keys, internal hostnames, or real directory data in source, fixtures, logs, reports, or exported blueprints.
- Use only synthetic test identities and data.
- Do not disable TLS verification as a normal mode. Support explicit CA references instead.
- Do not modify Gitea databases directly as a migration mechanism.

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
