# ADR 0029: Git return is a mapped, per-ref, non-force operation

## Status

Accepted

## Context

The forward import and the later offline repository can have different native repository IDs and
technical ref state. A return therefore cannot infer identity from a URL, repository name, numeric
ID, or the current storage location. It also cannot use a global instance timestamp to decide
whether the selected central data changed. A lost Git push response has the same ambiguity as any
other external write: repeating it without reconciliation can create an unintended effect.

## Decision

- Application-package schema v5 is the bounded Git-return contract. It carries the immutable
  forward package reference, stable repository origin, original A ref baseline, confirmed B import
  refs, desired B refs, and one verified bundle descriptor. It does not replace v1-v4 packages.
- The receiving installation resolves every repository through its existing instance-scoped replica
  mapping. Package facts cannot create or redirect that mapping.
- GitFerry compares ordinary branch and tag refs independently. New refs and proven fast-forward
  updates can become journal operations. Deletion, existing-tag replacement, non-fast-forward,
  protected, symbolic, internal, corrupt, missing, or identity-mismatched changes block before a
  Git push. No unchecked force update is permitted.
- Each semantic ref update has a stable journal identity derived from origin, target, repository,
  ref, baseline, desired object, and bundle evidence. Replay, restart, or repackaging cannot create a
  second operation for the same intent or change an existing intent.
- The Gitea call runs outside the SQLite transaction. Confirmed target evidence and the operation
  status are committed together. A lost response becomes `outcome_unknown`; reconciliation may
  confirm it only after the exact target instance, repository, ref, and object ID are observed.
- The package is exchanged as files between independent GitFerry state stores. Neither importer
  needs the opposite SQLite database or opposite Gitea during its offline phase.

## Consequences

The implemented flow returns bounded ordinary Git refs with exact object identity, per-ref drift
decisions, durable replay, and unaffected control repositories. It is not a merge engine and does
not provide a general exactly-once guarantee. Ref deletion, force updates, LFS return, wiki return,
submodules, partial-clone promises, and arbitrary server-side protected-branch policy migration are
outside this contract. A passing Git return does not qualify metadata return; that remains a
separate application flow.
