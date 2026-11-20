# Gitea connections

Sign in as the local GitFerry administrator and use **Connections** to register each Gitea endpoint.
Provide an HTTPS URL, a display name, an installation marker and credential/CA reference names.
The marker describes this installation lifecycle; use a different marker after rebuilding Gitea.
Display names do not establish identity. A successful connection test displays the Gitea version
and authenticated account; review these before approving the connection.

## Local files

Mount the Gitea access-token file under `GITFERRY_SECRET_DIR` (default
`/run/secrets/gitferry`). The alias entered in the UI is the filename, not its content or absolute
path. Files must be regular files, without symlink components, at most 4096 bytes, and inaccessible
to group/others. Grant the GitFerry runtime UID read access. The supplied Compose deployment sets
the directory to `/run/secrets`; its aliases are `source_api_token` and `target_api_token`.

For an internal CA, mount the PEM bundle read-only under `GITFERRY_CA_DIR` and enter its filename
as the CA reference. The default directory is `/run/gitferry/ca`; supplied Compose uses
`/etc/gitferry/ca`. Omit the reference to use the system CA store. TLS verification always remains
enabled. Redirects are rejected, so enter the final Gitea HTTPS base URL, including any subpath.

Missing files, incorrect permissions, invalid credentials and untrusted certificates produce a
failed connection check. Repair the local prerequisite and test again. Replaced certificate or
account evidence requires local review. Connection testing proves only version/account reads;
later planning must check the permissions needed for the selected transfer.

## Editing, rebuilding and portability

Editing requires the revision currently displayed. A concurrent edit requires reload; it cannot
overwrite a newer profile. **Retire** disables the profile while retaining all historical state.
**Replace installation identity** retires the old registration and creates a new one. Use this for
a rebuilt Gitea even if its URL is unchanged. Old mappings remain with the original instance.

**Export** downloads versioned JSON containing only endpoint metadata and reference names.
**Import** validates the complete file before saving any profile. Existing bindings and profiles
are never overwritten. Imported profiles require local files and local verification/approval.
The import file cannot grant administrator roles, change secret values or mark a transfer complete.

The same operations are available to a local operator with access to the state database:

```sh
gitferry connections --database /var/lib/gitferry/state/gitferry.sqlite3 list
gitferry connections --database /var/lib/gitferry/state/gitferry.sqlite3 create \
  --display-name 'Synthetic source' --base-url https://forge.example.invalid \
  --installation-marker installation-a --credential-ref source_api_token
gitferry connections --database /var/lib/gitferry/state/gitferry.sqlite3 export
gitferry connections --database /var/lib/gitferry/state/gitferry.sqlite3 import connections.json
```

Use `connections --database PATH test INSTANCE --expected-revision N` to obtain current evidence,
then `approve INSTANCE --expected-revision N --evidence-digest DIGEST` under the same command prefix.
Secrets are never command arguments. API routes under `/api/v1/connections` require administrator
authentication; mutations and checks additionally require the session-bound CSRF header.

These profiles do not yet constitute the guided transfer workflow. That is delivered by Issues
#9 and #10. The installation marker is operator-supplied; it is not a remotely attested Gitea ID.
