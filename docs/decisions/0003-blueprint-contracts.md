# ADR-0003: Separate versioned blueprint contracts

## Status
Accepted

## Date
2026-11-20

## Context
Instance configuration and transfer decisions have different lifecycles and security properties.

## Decision
Define independent `gitferry.io/v1` instance and transfer blueprint JSON Schemas, authored normally as YAML. Secrets use named references only. Unknown fields fail validation.

## Alternatives considered
- One combined document: rejected because it mixes reusable instance configuration with one transfer selection.
- Free-form YAML: rejected because errors and secret leakage would be detected too late.

## Consequences
Schema evolution requires explicit versions and compatibility tests. Gitea settings are included only when a documented configuration path has been verified.
