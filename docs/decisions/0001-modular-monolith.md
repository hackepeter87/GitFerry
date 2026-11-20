# ADR-0001: Modular monolith with durable local state

## Status
Accepted

## Date
2026-11-20

## Context
GitFerry needs durable background work but should avoid infrastructure dependencies beyond the specified stack.

## Decision
Use one Python modular monolith. FastAPI, the CLI, and future workers call the same application services. SQLite stores job and audit state; a persistent local volume stores large payloads.

## Alternatives considered
- Microservices: rejected because deployment, failure modes, and coordination costs are not justified.
- External queue/database: deferred until measured concurrency requires it.
- Browser-owned jobs: rejected because browser closure and container restart must not lose work.

## Consequences
State transitions require short explicit transactions and single-owner leases. Modules need stable ports so a future adapter or worker split remains possible.
