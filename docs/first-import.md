# Guided first import

The guided first-import flow is the administrator's normal path from source selection to a
reviewed, executable local plan. It remains bounded by the inventory and fidelity limits below;
this guide does not claim general migration completeness. See [transfer plans](transfer-plans.md)
for durable execution and prerequisite preview behavior.

## Source selection

The source browser uses an enabled, locally approved connection profile. It rechecks source
identity before reading a page and uses the existing local token and CA references.
The check captures token/TLS material once and reuses it for the catalog read, so a local
credential rotation cannot substitute an unverified account between those calls. It never
accepts a URL or credentials from a page request and performs no Gitea writes.

`POST /api/v1/connections/{key}/browse` requires an authenticated administrator and CSRF header:

```json
{"expected_revision":1,"kind":"organizations","organization":null,"page":1}
```

For repository selection, use `kind: repositories` and the selected organization name.
The equivalent CLI is:

```text
gitferry connections --database STATE_PATH browse INSTANCE_KEY --expected-revision 1 --kind organizations
gitferry connections --database STATE_PATH browse INSTANCE_KEY --expected-revision 1 --kind repositories --organization ORGANIZATION
```

Each page contains at most 50 entries and preserves native identifiers as strings. `next_page`
indicates another page may exist. A page is bounded to 2 MiB and a shared 15-second read deadline,
in addition to the existing connection-check deadline. Failures return no partial page. Local
profile revision/approval changes invalidate the read. The source API account needs access to
Gitea's administrator organization listing and the selected organization repositories.

The Gitea 1.27.2 [administrator organization endpoint](https://github.com/go-gitea/gitea/blob/v1.27.2/routers/api/v1/admin/org.go)
provides paginated organization identities. Repository display names and IDs are selection data;
source-provided clone URLs are not used as connection authority.

A namespace page is not a content inventory. `inventory_complete` remains false: metadata classes,
authors, rights, payloads and fidelity limitations must be inventoried and reviewed before an
export or import plan can be accepted. Browsing creates no object mappings, identity bindings or
executable operations. The subsequent export, package receipt, binding and plan steps are reached
through the panels below; no manual manifest or SQLite editing is the intended user workflow.

## Guided administrator workflow

Use the source selection, export, package intake and transfer-plan sections in order:

1. Choose a locally approved source connection, browse organizations and repositories, and
   explicitly select repositories. Source reads recheck the approved profile and never accept a
   page-supplied URL or credential.
2. Create or download the source export. Select the local recipient registration and whether
   supported metadata or membership proposals are included. Export jobs are durable and show
   prepared, queued, running, ready, or failed state.
3. Upload the archive through package intake. The receiver validates bytes, digests, paths and
   manifest relationships before admitting package facts. Admission is not target authorization.
4. Review and confirm the local sender/installation and each source realm. For each author, choose
   the explicit target account and credential reference; review missing bindings, permissions,
   collisions, unsupported items and waiting parents. Source claims never approve target identity
   or permissions.
5. Review the included metadata and choose whether to apply team/member changes. Review the
   generated selection and digest, then prepare a draft in Transfer plans. Review operations,
   actors, limitations, and target
   prerequisites. Request the read-only prerequisite preview before starting.
6. A passed preview is tied to the exact plan digest and generation. Start only after that gate.
   The worker rechecks prerequisites while holding the executor lock, then records durable
   operation states. Closing or refreshing the browser does not cancel queued work.
7. Refresh until the plan is confirmed, blocked, failed, or cancelled. On restart, confirmed
   operations replay as no-ops; an unknown write is blocked for explicit reconciliation and is
   never silently retried.

Changing or refreshing the source clears the preliminary selection. Loading or failed pages never
imply complete inventory or authorization to execute. A preview is evidence for review, not a
guarantee: execution performs its own checks before any write.

## Portable package file boundary (in progress)

An application package combines a versioned `package.json` selection descriptor with Git bundles
and an optional unchanged v1/v2 metadata manifest. Issue-author relationships are explicit in
the descriptor; normal comment authors and parents retain the v2 contract. Organization/team
entries and member selectors are claims for local review, never approved target permissions.
The schema is `schemas/v1/application-package.schema.json`; a synthetic structure-only example
is `examples/application-package.v1.json`. No metadata is fabricated for a Git-only selection.

The shared file service exposes developer/operator CLI commands:

```text
gitferry packages verify PACKAGE_DIRECTORY
gitferry packages pack PACKAGE_DIRECTORY --output TRANSFER.zip
gitferry packages receive TRANSFER.zip --output NEW_PACKAGE_DIRECTORY
```

Receipt checks declared file bytes, digests, paths, manifest relationships and author references
before publishing the directory. The default receiver bounds are 64 GiB of archive/payload input
and 10,000 entries; these are safety limits, **not a measured 30 GB acceptance result**. ZIP
compression, links, special files, undeclared files, duplicate names and path traversal are
rejected. Existing destinations are preserved. Reports say `validated-files-only` and
`execution_authorized: false`. No database, credential or network access is used by these commands.

These are application-core building blocks for the wizard. Administrators are not expected to
assemble descriptors manually in the final workflow. See
[ADR 0013](decisions/0013-application-package-and-file-receipt.md) for the trust boundary.
