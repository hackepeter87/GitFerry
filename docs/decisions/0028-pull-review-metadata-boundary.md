# ADR 0028: Pull-request metadata remains inventory-only until its typed dependencies are portable

## Status

Accepted

## Context

Gitea exposes pull requests, reviews, inline review comments, timelines and reactions through
different APIs and identity namespaces. Treating them as ordinary issues/comments would lose ref,
commit, diff-position, parent, author or ordering meaning. Gitea 28.4.0 exposes useful create and
state-transition routes plus a PR `content_version` guard, but route availability alone does not
establish a safe offline roundtrip. The guard rejects already stale content and performs a DB
content check; Gitea still documents a transaction gap around a multi-field PR PATCH.

## Decision

- Keep each class and typed parent visible in the selected-content inventory.
- Record the live request/response and route-method contract in the integration report.
- Keep all five classes outside `TRANSFER_CLASSES` and executable package operations for now.
- Put class-specific reasons in exported package limitations and render them in the desktop plan
  review before execution.
- Do not infer identity from title/body/time, do not invent a review or timeline origin, and do not
  use package absence as deletion authority.
- Require a future adapter to prove portable PR ref/repository mapping, explicit author binding,
  typed parent verification, stable origin/replica mapping, baseline/drift behavior and journaled
  crash recovery before changing any support status.

## Consequences

GitFerry can inventory and diagnose these classes without falsely claiming transfer. A package that
contains repositories with them remains an explicitly limited transfer rather than a complete
metadata migration. The limitation is visible and repeatable after restart. Implementing PR and
review transfer later requires a new versioned package contract; existing manifest/application
package bytes and meanings remain unchanged.
