# ADR-0002: Git bundles plus versioned manifests

## Status
Accepted

## Date
2026-11-20

## Context
Offline transport must preserve Git refs/history, scale to large repositories, and permit independent integrity checks.

## Decision
Store each repository as a Git bundle and describe all artifacts in a versioned manifest with sizes and SHA-256 digests. Metadata is separate structured data. Add asymmetric manifest signatures before production support.

## Alternatives considered
- Bare repository directory copy: rejected as less portable and harder to validate as one artifact.
- Gitea database copy: rejected because it couples deployment internals and bypasses documented interfaces.
- One compressed archive only: rejected because it obscures per-artifact integrity and streaming limits.

## Consequences
LFS, wikis, attachments, and release assets remain separate capabilities. A valid ordinary Git bundle proves none of them.
