# Security policy

## Reporting

Do not open a public issue for a suspected vulnerability. Use GitHub's private security advisory workflow for this repository.

## Supported versions

GitFerry has not released a supported production version. The current repository is a feasibility iteration and must not be used for productive migrations.

## Security invariants

- Exported blueprints contain secret references only.
- Imported YAML, JSON, archives, manifests, paths, and API responses are untrusted.
- Package extraction must reject absolute paths, traversal, links escaping the package root, oversized entries, and decompression bombs.
- TLS verification remains enabled; private CAs are supplied by explicit references.
- Logs and reports must redact credentials and tokens.
- Imported identities never gain permissions implicitly.
- GitFerry never manages its test environment through a mounted Docker socket.

The iteration-1 threat analysis is in `docs/architecture.md`; package/path validation tests are part of `make test`.
