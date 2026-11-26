# ADR 0026: Separate the Gitea test baseline from runtime compatibility

Status: accepted

## Context

GitFerry originally used Gitea 1.27.2 in every live integration probe. That immutable pin made
tests reproducible, but treating it as an application requirement would incorrectly bind the
product to one server release and would let historical evidence stand in for current compatibility.

## Decision

- The primary integration matrix pins the official multi-platform Gitea 28.4.0 OCI index by
  digest. Reports record the configured pin, observed server version, resolved platform and image
  digest.
- The application does not require one exact Gitea version. It accepts stable numeric Gitea
  versions, records the observed version with captured facts, and fails when the API behavior or
  capability needed by an operation is not available.
- Inventory schema v3 carries stable observed versions and the current native-payload facts.
  Existing v1/v2 documents and immutable manifest fixtures retain their original meaning and
  identity.
- Historical 1.27.2 evidence remains dated evidence. A separate, bounded compatibility probe
  exercises a 1.27.2 source and 28.4.0 target; it does not imply support for downgrades, in-place
  database upgrades or every intermediate release.
- Version-specific test infrastructure configuration, such as Gitea 28 migration egress policy,
  stays in the integration matrix and is not a product runtime lock.

## Consequences

Every claimed Gitea capability still needs live evidence on a named source/target version pair.
Adding a new test baseline may require a new inventory schema only when the portable facts change,
not merely because the server version changes. Unsupported API behavior remains a visible blocker;
GitFerry does not silently fall back to weaker transfer semantics.

The primary 28.4.0 same-version suite and the bounded 1.27.2-to-28.4.0 migration probe are separate
evidence. Neither establishes a general compatibility range.
