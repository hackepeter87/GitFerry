# Requirements and assumptions

## Confirmed product requirements

The authoritative input is the project owner's initial assignment. Iteration 1 must establish a modular Python/FastAPI, Vue/TypeScript, SQLite, native-Git architecture; versioned instance and transfer blueprints; reproducible two-Gitea tests; and executed Git-bundle plus issue/comment feasibility probes.

The product workflows are `bootstrap`, `provision`, and `roundtrip`. Transfers must be selective, collision-aware, resumable, idempotent, integrity-checked, auditable, offline-capable, and stream large data rather than loading it all into memory. These are target properties; only properties evidenced in the capability matrix are currently supported.

## Iteration-1 assumptions

- Gitea 1.27.2 is the first tested community release because its exact image and versioned official API documentation are available.
- SQLite is suitable for one GitFerry control-plane instance with serialized state transitions; repository payloads remain on the filesystem.
- Native `git bundle` is the repository-content transport primitive. Gitea REST is used only for documented metadata operations.
- Target repositories and organizations are initially absent, except for a technical administrator and deliberately seeded ID offsets.
- Synthetic source and target identities may have different numeric IDs; logical mapping uses explicit stable keys, never email alone.
- Iteration-1 signatures are designed in the manifest but not implemented; SHA-256 is exercised now.

## Risks and unresolved evidence

1. Gitea REST create endpoints may not preserve every original author, timestamp, number, state transition, review, or relation. Each field needs a real-instance probe before support is promised.
2. Pull-request refs and review history are not ordinary Git refs and require separate API behavior tests.
3. LFS objects, wikis, release assets, and issue attachments require distinct transport and isolation tests.
4. Authentication-source configuration may require CLI/config-file management; no REST configurability is claimed.
5. LDAP compatibility does not prove Active Directory compatibility. AD-specific evidence requires a dedicated environment.
6. A 30 GB load claim requires a separately executed benchmark with disk and peak-memory measurements.
7. Offline isolation must deny source and Internet reachability from the importer, not merely use different container names.
8. The iteration-1 validation API has no administrative authentication yet and must not be exposed as a production service. Authentication design and tests remain required before release.

## Explicitly deferred

Full transfer engine, production LDAP provisioning, complete web UI, productive-data migration,
GitLab integration, package signatures, general multi-operation resume/reconciliation, and the 30 GB
qualification run. The bounded issue-operation restart and portable-manifest replay slices are
implemented and must not be generalized to these deferred capabilities.
