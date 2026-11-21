# ADR 0024: Selected-content inventory and transfer completeness

Status: proposed for Issue #17

## Context

The current source catalog lists selection pages, and source capture transfers a bounded set of
Git/structure/issue/comment data. Neither is a complete remote inventory. Existing incomplete
flags must not become a completeness claim merely because the application can execute those
operations. The pinned Gitea 1.27.2 API has both paginated and non-paginated lists, classes without
numeric identifiers, and no general REST enumeration of LFS objects.

## Decision

Add a versioned inventory document for the selected repositories. Every agreed class has an
explicit observation status, count or unknown count, object namespaces/relationships, available
size facts, transfer capability and prerequisites. Observation completeness and transfer
completeness are different values. Explicit exclusions do not mean zero objects or deletion.

Use the existing bounded source reader, approved connection profiles, local SQLite transactions
and single background executor. Inventory jobs make no remote writes. Their intent survives a
restart; an interrupted unconfirmed read may be repeated, while a confirmed snapshot is immutable.
New observations receive a new snapshot identity. The local content digest uses the existing
`canonical_json` encoding (UTF-8, sorted keys, compact separators, Unicode preserved) over all
validated document fields. It is explicitly a project content digest, not a standard canonical-JSON
algorithm, signature or manifest-byte digest. Enclosing package bytes retain their own digest. Incomplete/failed observations remain durable
for diagnosis and cannot authorize a linked export or import plan.

Bind a reviewed snapshot to the registered installation marker, source profile evidence, selected
native repository identities, comparison-rule version and exact facts. Revalidate selected facts
before using it for source export. Changes to unrelated repositories do not invalidate it. Never
use an instance-wide timestamp or current observation to replace the reviewed baseline.

Keep manifest v1/v2 schemas, bytes, origins and digests unchanged. A versioned application-package
extension carries portable inventory facts separately from manifests and local execution state.
Dispatch existing package versions explicitly; legacy packages remain clearly identified as
bounded selections without complete-inventory evidence. Imported inventory is untrusted data,
not a credential, local binding, administrative approval or proof of authenticated provenance.

Use strict bounded pagination and identity checks. Missing totals/pages, duplicate identities,
permission failures, unsupported endpoints and limit exhaustion remain visible. Size estimates
identify API units, known bytes, unknown payload sizes and temporary-space assumptions; estimates
are not enforced disk quotas or a 30 GB qualification.

## Consequences and verification

The desktop, REST API and CLI present the same stored facts and decisions. A complete transfer
cannot be claimed for excluded, unknown or unsupported requested content. Real synthetic fixtures
and direct test-harness enumeration must qualify available classes before this ADR is accepted.
Old manifest fixtures and supported bounded first-import behavior remain regression gates.
No additional content transfer capability is claimed by inventory support alone; Issues #18–#20
own their existing feasibility and implementation contracts.
