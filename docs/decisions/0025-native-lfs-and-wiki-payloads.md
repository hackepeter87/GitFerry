# ADR 0025: Native LFS and wiki payloads in the existing application plan

Status: Proposed — implementation and real-instance qualification in issue #18.

## Decision

Use the existing inventory, source-capture, package-admission, plan and operation
journal boundaries. Do not introduce a second transfer engine or synchronize state
databases. Ordinary Git refs, LFS objects and wiki repository refs remain separate
capabilities. Only explicit selections enter a reviewed plan.

Content-inventory v2 records standard LFS SHA-256 object identities discovered in
all reachable historical blobs, and one native wiki repository with its exact ref
map. Inventory v1 retains its page-based wiki observations and comparison rules.
Application-package v3 carries these facts and all selected payload sizes and byte
digests. Transport manifests v1/v2, application packages v1/v2 and their existing
bytes and digests are unchanged. Schema versions are independent of application
versions and SQLite schema versions.

LFS files use canonical content-addressed paths. Every referenced object must be
available during inventory and have verified bytes before publication. The importer
re-enumerates bundled Git pointers and matches them to the selected object set.
Only qualified same-origin Gitea basic-transfer actions are accepted; redirects,
external storage URLs, pointer extensions and unconfigured discovery endpoints are
blocked. A download-batch POST is a read protocol operation, not an object upload.
The pinned Gitea 1.27.2 verify action advertises a weighted Accept header; the adapter
accepts that exact observed value without relaxing endpoint identity checks.

Wiki history is a separate Git bundle. Gitea requires native initialization before
the first Git push. A fixed alphanumeric technical page initializes that repository.
GitFerry confirms the resulting native commit only if it is the sole root commit,
contains only the expected technical page, and has exactly the expected content.
Preexisting or concurrent history prevents confirmation and subsequent publication.
A title match or API 404 does not prove native identity or emptiness. Disabled,
external and uninitialized source wikis remain explicit blockers; empty wiki
initialization is not yet qualified. Branch conversion and ref deletion are absent.

The wiki publication uses an atomic Git push with explicit per-ref leases against
the confirmed bootstrap refs. The desired refs must include every existing ref;
there is no force-mirror deletion. Ref and repository identities are checked again
after publication. A first-import plan cannot reuse this path for changed wiki refs;
return requires a separate baseline and conflict contract.

## Local transaction and recovery

SQLite migration 17 extends the existing operation-kind constraint with LFS upload,
wiki bootstrap and wiki bundle publication. Native observed facts are stored in the
existing operation event together with mapping confirmation and completion, within
one transaction. Existing schema-16 records, foreign keys and unknown operations
survive migration. No credentials enter journal records.

The existing executor lock owns all external writes. Repository creation precedes
native payload work, and LFS confirmation precedes ordinary Git ref publication.
Confirmed operations survive restart. A missing response or interruption before
local confirmation remains outcome_unknown; a new package does not clear it.
Explicit reconciliation reads the mapped target and verifies content-addressed LFS
bytes or exact wiki refs. Bootstrap resolution additionally requires an explicit
native commit SHA and the root-history proof. Generic unverified journal resolution
is not accepted for these native operations.

No cross-system exactly-once guarantee is claimed. Read-before-write checks do not
freeze remote administrators. A wiki bootstrap POST may have added a technical page
before conflicting history is detected; the operation remains unknown and no
subsequent bundle push is allowed. The operator must inspect that result.

## Bounds and evidence

The existing configured capture byte bound also limits native payloads. Pointer,
object, ref, JSON and archive counts are bounded. Bytes are streamed and hashed;
paths, symlinks, hardlinks and digests are checked before target writes. Capture
publishes only the complete immutable package. Workspace estimates remain estimates,
not filesystem quotas; the later capacity milestone owns the large-data acceptance.

Issue #18 and its Actions artifacts must distinguish adapter probes, complete
application flows, restart/fault evidence and browser checks. Passing primitive or
mocked tests alone does not establish a supported product capability. Native Git
commit metadata is separate from Gitea's historical API timestamp limitations.

Native ref maps are typed data (`refs/*` to 40/64-character hexadecimal object IDs),
not configuration field names. The persisted secret-field guard exempts only this
exact validated map shape, so legitimate ref names containing words such as
`secret` remain usable; malformed maps and actual credential fields still fail.
Wiki push confirmation stores the observed ref count and canonical JSON SHA-256;
full comparison facts remain in the immutable operation intent. This keeps the
confirmation/reconciliation record below its evidence bound even for 10,000 refs.
Repackaging may reuse operations only when the complete native intent is unchanged,
including bundle path, size and digest. A different bundle encoding requires an
explicit conflict decision even if its refs match; it cannot inherit an old success.
