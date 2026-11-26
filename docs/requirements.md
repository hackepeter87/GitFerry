# Requirements and assumptions

## Confirmed product requirements

The authoritative inputs are the project-owner mandate and the versioned GitHub roadmap. The
completed foundation established a modular Python/FastAPI, Vue/TypeScript, SQLite, native-Git
architecture; versioned instance and transfer blueprints; reproducible two-Gitea tests; and
executed Git-bundle plus issue/comment feasibility probes. Those components remain the
implementation base rather than a disposable prototype.

The product workflows are `bootstrap`, `provision`, and `roundtrip`. Transfers must be selective,
collision-aware, resumable, idempotent, integrity-checked, auditable, offline-capable, and stream
large data rather than loading it all into memory. These are target properties; only properties
evidenced in the capability matrix are currently supported.

GitFerry must become an installable administration application for file-mediated transfer between
separate Gitea environments. It supports temporary offline work with controlled return and a
one-time provisioning mode without source-user creation. Normal operation uses a protected web UI;
REST and CLI call the same application core. Configuration is versioned and portable without
embedded secrets. The OCI application uses persistent local state and package/work directories and
does not require an external database service or an open browser.

The agreed data scope is tracked field by field in `docs/capability-matrix.md`. Each class records
implementation, first-import and return status independently, cites its evidence and has exactly one
owning roadmap issue. Untested classes do not disappear from the product target, and module tests do
not become workflow support without the required real-system evidence. Gitea is the first platform.
Adapter boundaries may support future forges, but GitLab implementation is not part of this roadmap.

## Foundation assumptions

- Gitea is the first supported forge. The application is not pinned to an exact Gitea release;
  it validates a stable observed version and records the API evidence used by each run. The
  reproducible primary integration baseline is Gitea 28.4.0 at OCI index digest
  `sha256:aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309`. Gitea 1.27.2
  remains historical compatibility evidence and is not silently rewritten.
- SQLite is suitable for one GitFerry control-plane instance with serialized state transitions; repository payloads remain on the filesystem.
- Native `git bundle` is the repository-content transport primitive. Gitea REST is used only for documented metadata operations.
- Target repositories and organizations are initially absent, except for a technical administrator and deliberately seeded ID offsets.
- Synthetic source and target identities may have different numeric IDs; logical mapping uses explicit stable keys, never email alone.
- The existing manifests are integrity-checked with SHA-256 but not authenticated. A signed outer
  envelope is planned without changing historical manifest bytes.

## Risks and unresolved evidence

1. Gitea REST create endpoints may not preserve every original author, timestamp, number, state transition, review, or relation. Each field needs a real-instance probe before support is promised.
2. Pull-request refs and review history are not ordinary Git refs and require separate API behavior tests.
3. LFS objects, wikis, release assets, and issue attachments require distinct transport and isolation tests.
4. Authentication-source configuration may require CLI/config-file management; no REST configurability is claimed.
5. LDAP compatibility does not prove Active Directory compatibility. AD-specific evidence requires a dedicated environment.
6. A 30 GB load claim requires a separately executed benchmark with disk and peak-memory measurements.
7. Offline isolation must deny source and Internet reachability from the importer, not merely use different container names.
8. The first local-administrator boundary is implemented with host-controlled bootstrap/recovery,
   durable revocable sessions and protected API writes. It is not external SSO, MFA or a
   multi-administrator lifecycle, and the application still requires HTTPS before non-local use.

## Outside the approved roadmap

Productive-data migration, GitLab or another forge adapter, a distributed general job platform,
and productive installation are not authorized by this roadmap. Stable public release remains a
separate owner decision. Package signatures, multi-operation resume, guided UI, directory
integration, and the 30 GB qualification are no longer silently deferred: they have explicit
milestones and issues, while their current status remains unsupported or untested until executed.

## Desktop administration scope

The owner clarified that GitFerry is operated on desktop computers. The first-import UI is
qualified at 1024×768 and 1440×900 CSS-pixel viewports. Keyboard access, visible focus, readable
errors, and the complete real-backend workflow remain acceptance requirements. Mobile/touch
qualification is not a release requirement; this clarification does not reduce transfer,
identity, security, durability or data-fidelity requirements.
