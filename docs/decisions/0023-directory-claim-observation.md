# ADR 0023: Read-only directory claim observations

Status: bounded component; directory and Active Directory qualification remain separate.

## Decision

Directory claim refresh uses a small, read-only LDAP client boundary. The client receives a
validated local configuration and an already-resolved password in memory; it never resolves
credentials, places them in argv, logs them, or changes a directory. The result is a
`DirectoryClaimSnapshot` containing the realm, login, external identifier, DN/OU path, optional
email, and separately observed groups. It is evidence for the identity-decision layer, not an
approval, binding, account selection, or permission grant.

The transport requires LDAPS or explicit StartTLS, a CA bundle, certificate validation, bounded
connect/receive/search timeouts, a two-entry user result limit, and a 64-group limit. Referral
following, schema/autodiscovery, and write operations are disabled. Login and group-DN values are
escaped as LDAP filter values. Groups may be read through a bounded direct group search
(`member`/DN by default) or a configured user attribute. Missing, ambiguous, multivalued,
malformed, disabled-filter, stale, and transport-failure cases fail closed with safe diagnostics.
Binary `objectSid` values are only normalized to a bounded textual SID; this does not claim Active
Directory support.

The injectable transport boundary is deliberate: unit tests verify filter construction,
normalization, bounds, and failure policy without presenting a fake LDAP server as qualification
evidence. The real OpenLDAP fixture remains the LDAP evidence path. A future Samba AD-compatible
fixture must separately prove SID, domain-login, disabled-account, and nested-group behavior;
Microsoft AD requires a distinct licensed/authorized environment.

## Dependency and security evidence

`ldap3` is pure Python and exposes the required `Server`, `Tls`, `Connection`, `start_tls`,
`read_only`, `auto_referrals`, `receive_timeout`, `time_limit`, and `size_limit` controls. The
installed stable release is pinned to `ldap3==2.9.1`. The existing HTTP client and standard
library do not implement LDAP. A pure Python client avoids a second system client/credential
file process boundary and the native build dependencies of python-ldap. No hand-written LDAP
protocol is introduced. Unit tests inject transport behavior; real qualification uses the
installed client. Its Linux socket receive timeout is an integer, as required by its API.

References: [ldap3 2.9.1 on PyPI](https://pypi.org/project/ldap3/2.9.1/), [ldap3 Connection
API](https://ldap3.readthedocs.io/en/latest/connection.html), and [ldap3 TLS
API](https://ldap3.readthedocs.io/en/latest/ssltls.html).

## Limits

Gitea 1.27.2 authentication-source list/readback exposes source metadata, not arbitrary LDAP
external IDs, OU paths, or a claim snapshot. Native Gitea source/account correlation therefore
is checked against the authenticated functional account's nonzero source ID and authentication
login. The administrator explicitly selects the local directory configuration and reviews its
separate target claim. This is an explicit local association, not proof that all remote Gitea
source settings equal that configuration. A claim observation never compares a source realm's
SID to a target realm's SID.

Candidate evidence stores the selected configuration revision/digest and target facts in the
existing versioned local evidence JSON; no SQLite or transport version change is required.
Approval re-reads both systems and checks the configuration again inside the local transaction.
Execution checks current target facts outside long SQLite write transactions. Changed ID, DN/OU
or observed groups creates a durable conflict; an outage blocks writes without changing bindings.
Email remains descriptive. Re-reading restored facts cannot clear an existing conflict.
Neither observation nor approval creates a directory account or grants repository membership.
