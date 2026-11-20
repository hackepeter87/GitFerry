# Contributing

GitFerry accepts small, reviewable changes with explicit tests and honest capability reporting.

1. Open an issue describing the behavior and evidence needed.
2. Keep changes within one independently verifiable slice.
3. Add or update tests before implementation where behavior changes.
4. Run `make test`; run `make test-integration` for Gitea-facing behavior.
5. Update documentation and the capability matrix when evidence changes.
6. Never add production credentials, real internal domains, or personal data.

Commit messages should use `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, or `chore:` prefixes. A passing mock test cannot replace a required real-Gitea integration test.
