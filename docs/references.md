# Verified upstream references

Implementation and feasibility assumptions were checked against these official Gitea 1.27 sources on 2026-11-20:

- [API usage, authentication, pagination, and instance OpenAPI](https://docs.gitea.com/1.27/development/api-usage/)
- [Create a user](https://docs.gitea.com/api/1.27/operations/admin-create-user/)
- [Create an organization repository](https://docs.gitea.com/api/1.27/operations/create-org-repo/)
- [Create a team](https://docs.gitea.com/api/1.27/operations/org-create-team/)
- [Add a repository to a team](https://docs.gitea.com/api/1.27/operations/org-add-team-repository/)
- [Repository and organization permissions](https://docs.gitea.com/1.27/usage/access-control/permissions/)
- [Create an issue](https://docs.gitea.com/api/1.27/operations/issue-create-issue/)
- [Add an issue comment](https://docs.gitea.com/api/1.27/operations/issue-create-comment/)
- [Edit an issue and optimistic content version](https://docs.gitea.com/api/1.27/operations/issue-edit-issue/)
- [Migrate a remote repository](https://docs.gitea.com/api/1.27/operations/repo-migrate/)
- [Migration allowlist and private-network configuration](https://docs.gitea.com/1.27/administration/config-cheat-sheet/#migrations-migrations)
- [Docker installation and exact-version image usage](https://docs.gitea.com/1.25/installation/install-with-docker/)

Documentation describes the contract; `docs/testing.md` and the live `/swagger.v1.json` evidence record what the pinned Gitea 1.27.2 instances actually did. Runtime evidence takes precedence when request and response schemas or observed behavior differ.

## SQLite control state

- [SQLite foreign key support](https://www.sqlite.org/foreignkeys.html) — enforcement is enabled
  and verified separately on every connection.
- [SQLite write-ahead logging](https://www.sqlite.org/wal.html) — local-host constraint, checkpoint
  model, and `-wal`/`-shm` companion files.
- [SQLite `synchronous` pragma](https://www.sqlite.org/pragma.html#pragma_synchronous) — WAL with
  `FULL` durability semantics.
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html) — one simultaneous writer and
  `BEGIN IMMEDIATE` behavior.
- [Python `sqlite3`](https://docs.python.org/3/library/sqlite3.html) — standard-library driver and
  parameter binding.
