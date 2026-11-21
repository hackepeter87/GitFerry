# Guided first import

Implementation is in progress under Issue #10. The complete workflow is not yet accepted.
The durable plan service is already available; see [transfer plans](transfer-plans.md).

## Source selection

The source browser uses an enabled, locally approved connection profile. It rechecks source
identity before reading a page and uses the existing local token and CA references. It never
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
executable operations. The remaining export, package receipt, binding and end-to-end UI steps are
tracked by Issue #10; no manual manifest or SQLite editing is the intended user workflow.

## Current browser step

Choose a locally approved source, then an organization and repositories. Source reads start only
after explicit selection. Selections survive page and organization navigation within this view;
the summary supports removing an earlier selection. Changing or refreshing the source clears
the selection. Reloading the browser also clears this preliminary selection; no durable transfer
has been submitted yet. Loading and failed pages never imply complete inventory or authorization
to execute. Export/import execution remains unavailable until the subsequent steps are delivered.
