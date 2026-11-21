# Selected-content inventory

GitFerry inventories the exact repositories selected by the operator before export. The
inventory is a read-only observation, not permission to write to a target and not a claim that
every Gitea feature is transferable.

## Operator surfaces

The same durable job is available through the administrator REST API, the desktop inventory
workflow, and the CLI:

```sh
gitferry inventories create --source-instance source --source-revision 1 \
  --repository synthetic/selected=7 \
  --class git_refs --class organizations --class teams --class permissions \
  --class identities --class issues --class comments --inventory-id selected-inventory
gitferry inventories list
gitferry inventories show --inventory-id selected-inventory
gitferry inventories document --inventory-id selected-inventory
gitferry inventories run-next
```

`create` requires `--source-instance`, `--source-revision`, at least one repeated
`--repository SCOPE=NATIVE_ID`, and at least one repeated `--class`. `show` and `document`
require `--inventory-id`; all commands accept `--database` when the configured state database is
not used. The API is under `/api/v1/inventories`: `GET` lists, `POST` creates, `GET /{id}` shows,
`GET /{id}/document` downloads the confirmed document, and `GET /capabilities` reports the
versioned class catalog.

## Class catalog and status

Every selected repository has all 21 classes, even when a class is excluded, unsupported, or
blocked: Git refs, LFS, wiki, organizations, teams, permissions, identities, issues, labels,
normal comments, attachments, releases, release assets, pull requests, pull comments, reviews,
review comments, timelines, reactions, milestones, and other content.

The current qualified contract observes 19 live classes when the selected source exposes the
corresponding objects. LFS and `other_content` remain explicitly unknown/not-supported; they are
not reported as empty. A requested class with a missing page, permission failure, inconsistent
identity, unsupported endpoint, or stale source is blocked/failed with an unknown count. An
unrequested class is visible as excluded. Absence in a package never means delete.

Requested label transfer, issue label/milestone/assignee relationships, split or additional team units, direct
collaborators, and other unsupported requested content block the selected transfer scope.
Technical administrative owner teams remain an explicit exclusion; target ownership is local. Inventory does not add a metadata
transfer claim, a signature, a hard disk quota, or a 30 GB qualification.

## Immutability and re-observation

The confirmed document is bound to the registered source installation, profile revision, exact
repository native IDs, selected classes, and snapshot identity. Its canonical content digest is
stored with the job. Before export, GitFerry re-observes the selected facts and rejects changed
or stale inventory; the export path rechecks the selected facts before and after capture. A new
observation receives a new snapshot identity. SQLite schema 16 stores the durable job and digest.

## Package relationship

Application-package v2 carries `inventory/document.json` with its byte SHA-256, a semantic/content
digest, and the `include_members` decision. It keeps current native source-copy IDs distinct from
portable object origins. Existing manifest v1/v2 formats and package v1 remain unchanged.
Legacy packages and legacy export requests are explicit bounded selections; they do not acquire a
complete-inventory claim retroactively.

Inventory facts are untrusted source observations. They do not approve identities, grant target
permissions, authenticate a sender, or authorize execution. Re-review a ready inventory before
using it in an export or package plan.


## Explicit native payload selection

The desktop export options **Include Git LFS objects** and **Include native wiki
history** invalidate prior inventory approval. Select them before inspecting and
reviewing the inventory. They use source request v3, inventory v2 and application
package v3; these versions are independent of transport manifest v1/v2 and SQLite.

LFS discovery includes standard v1 pointers in all selected reachable history, not
only the current checkout. Every required object must be available; export then
streams and verifies its bytes. Native wiki selection carries its separate Git
history/ref set. Disabled, external and uninitialized wikis currently block this
selection; an absent API result is not proof that the wiki is empty. The plan shows
these limitations before export. Do not omit a required content class to call an
incomplete transfer complete.

The target must enable HTTP Git and LFS where selected. Wiki bootstrap also checks
the newly created/mapped target repository's actual default branch before its
POST; that branch must exist in the selected wiki refs. A blocked native step may
leave already-confirmed organization/repository setup intact. It does not authorize
ref deletion or automatic branch renaming.
