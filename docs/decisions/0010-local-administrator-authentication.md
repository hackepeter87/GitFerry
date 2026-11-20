# ADR-0010: Local administrator authentication

## Decision

GitFerry uses one local administrator account, created once from the host CLI:

```text
gitferry admin bootstrap --database <state-db> --username <username> --password-file <file>
```

The password file must be a regular, non-symlink file, no larger than 4096 bytes, not writable by
group or other users, and contain one UTF-8 line. Bootstrap is durable and subsequent bootstrap
attempts fail. Recovery uses the same account and a new protected password file:

```text
gitferry admin recover --database <state-db> --password-file <file>
```

Recovery changes the password and revokes every existing session. `gitferry admin status` and
`GET /api/v1/auth/status` expose only whether bootstrap is required.

Passwords are stored as PBKDF2-HMAC-SHA256 using a random 16-byte salt, 600,000 iterations, and a
32-byte derived key. Session cookies contain an opaque `session-id.verifier` value; only SHA-256
hashes of the verifier and CSRF token are durable. Sessions expire after eight hours and are
checked server-side for expiry, revocation, password change, and disabled accounts. Login failures
are durably limited to 10 attempts per hashed client key in a 15-minute window.

The secure deployment cookie is `__Host-gitferry_session` with `Secure`, `HttpOnly`, `SameSite=Strict`,
`Path=/`, and no `Domain`; the CSRF cookie is `Secure`, `SameSite=Strict`, and not `HttpOnly` so
the same-origin Vue client can send its value in the CSRF header. State-changing requests require
that session-bound CSRF check. Health (`/healthz`), readiness (`/readyz`), and the minimal auth
status are public; protected API routes fail closed when authentication is absent or unavailable.

The packaged OCI image serves the built same-origin Vue frontend. The browser tests use synthetic
identities and exercise bootstrap guidance, keyboard login, denied credentials, authenticated
state, logout, expiry, and recovery revocation.

## Limits

This is local single-administrator authentication. MFA, SSO, and multi-administrator management are
not implemented. TLS termination is required for deployed secure cookies. There is no general
transfer UI or claim of productive-data migration in this decision.
