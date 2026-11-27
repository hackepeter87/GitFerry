# Complete 0.4 content-scope acceptance

GitFerry accepts the 0.4 scope by reconciling all agreed classes, not by treating a passing test
total as coverage. `tests/integration/scope_acceptance.py` builds the machine-readable
`scope_acceptance` section only from facts produced by the live integration probes. The report
contract rejects missing, contradictory or overstated entries.

## Reconciliation model

For every class the catalog records:

- the live inventory status and reconciled count;
- first-import and return support as separate decisions;
- whether the corresponding target or pre-write block was verified;
- payload-integrity applicability;
- the owning release, evidence sections and the remaining limitation.

The generic inventory directly observes 19 classes. LFS is deliberately `unknown` there because
Gitea's repository API is not a complete LFS object inventory; its pointer and object bytes are
qualified by the separate native-payload probe. `other_content` remains unknown and blocked.

## Accepted classes and boundaries

| Classes | First import | Return | Acceptance boundary |
| --- | --- | --- | --- |
| Git refs, organizations, teams, permissions, identities | supported with limits | not implemented | Selected structures and explicit local bindings only; no deletion semantics |
| Issues and normal comments | supported with limits | supported with limits | Stable origins, replay and drift rules; native historical timestamps remain separate |
| Labels, attachments, releases and release assets | supported with limits | not checked | Qualified create/update operations and payload bytes; replacement/deletion and complete return remain open |
| LFS and wiki | supported with limits | not implemented | Native payload path with digest/ref reconciliation; no guided return plan |
| Milestones and pull comments | not supported | not supported | Inventory-only; selection blocks before writes |
| Pull requests, reviews and inline review comments | not supported | not supported | Field behavior is qualified, but portable refs/authors/positions/baselines are unresolved |
| Timelines and reactions | not supported | not supported | Timeline is read-only; reactions lack stable native identity and historical-time input |
| Other content | not supported | not supported | Unknown content blocks a complete-transfer claim |

`complete_transfer_claimed` is therefore always false for this milestone and
`limited_scope_required` is true. An administrator may execute only the explicitly supported
selection shown by the desktop preflight. A missing package entry never means deletion.

## Evidence and trust boundary

The same integration run uses the exact pinned Gitea 28.4.0 source and target images. It verifies
the supported target observations, native payload digests, replay/restart behavior, omitted-metadata
non-deletion and an unchanged control repository. The desktop browser test proves that an
unsupported selection remains visible and cannot be exported.

The report contains synthetic facts only. Review approval, CI run identity and immutable candidate
digest remain delivery evidence in the pull request, issue and Actions artifacts; they are not
embedded later into repository documentation.
