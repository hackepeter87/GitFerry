# ADR 0022: Explicit target-local directory bootstrap

Status: investigation and bounded implementation; acceptance remains open in #13 and #15.

## Interface evidence

Gitea 1.27.2 provides `gitea admin auth add-ldap` and `add-ldap-simple`, plus list/update/delete.
The [versioned CLI documentation](https://docs.gitea.com/1.27/administration/command-line/)
and [pinned command implementation](https://github.com/go-gitea/gitea/blob/v1.27.2/cmd/admin_auth_ldap.go)
are the contract. Authentication-source REST configuration is not assumed.

The BindDN command accepts a bind password through `--bind-password`; it has no supported
password-file/stdin flag. GitFerry must not put that secret in process arguments. A standalone
`auth.d` configuration is not a supported alternative. Gitea's authenticated administrator
UI is a supported password-bearing path, but is not represented as an implemented REST API.

## Bounded implementation

The first command adapter prepares LDAP simple authentication: users bind with their own DN
and password at login, so bootstrap requires no directory bind password. The existing instance
blueprint gains an explicit `mode: simple` and user-DN/filter/attribute fields. Existing BindDN
blueprints remain schema-compatible; their automated execution is explicitly unsupported until
a safe supported interface is implemented. This is an open capability, not permission to discard
the product requirement.

Only LDAPS or explicit StartTLS is allowed. A local CA reference resolves outside exported
configuration; `SSL_CERT_FILE` supplies the trust store for the target-local process. The running
Gitea service must also use the trusted CA. A successful CLI command does not prove that the
running service has the right CA or that login works.

The adapter is not a general shell or remote execution service. Binary/config/CA paths are
host-local prerequisites, never authority supplied by a transport package. Creation needs the
Gitea server's local administrative access. Version, source identity, native login and effective
permissions require separate readback/functional checks.

SQLite schema 15 stores revisioned local directory configuration separately from the bootstrap
journal. Authenticated REST and the desktop edit configuration; the host CLI invokes the same
configuration service and a fixed-command target-local adapter. The journal shares the existing
single-writer lease. It is local execution state, never a transport-manifest approval. Its states
are `prepared → executing → confirmed` or `outcome_unknown`; an abandoned `executing` row becomes
unknown before another execution attempt. Conflicting intents cannot overwrite an uncertain row.
No SQLite transaction is held across the external Gitea process. The native result and journal
completion are committed together; this is not cross-system exactly-once execution.

The isolated bootstrap fixture explicitly mounts the target server volume into a separate local
administration context and uses the identical binary from the pinned Gitea image. File importer
contexts receive no such mount. Only Gitea's documented CLI reads/writes its own database; GitFerry
does not implement a Gitea database adapter. See [the operator guide](../directories.md).

`admin auth list` exposes identity/name/type/activation, not all configuration. It cannot prove
an unchanged filter, CA or DN by itself. Reusing a recorded intent digest must never be presented
as complete server configuration verification. Unknown creation outcomes must remain stopped
until a unique supported reconciliation is available.

## Evidence boundaries

The isolated OpenLDAP fixture is LDAP evidence only. Equal UID values in two bases do not imply
two equal Gitea usernames can coexist; native account namespace behavior must be measured.
Missing email in GitFerry mapping and Gitea's account-creation email requirements are distinct.
Samba AD-compatible or Microsoft AD behavior requires a named separate environment; neither is
inferred from a successful OpenLDAP login. No direct Gitea database mutation is permitted.
