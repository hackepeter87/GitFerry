# OCI installation and first import

This Compose example runs the same-origin administration frontend and durable application worker.
Use the [guided first-import workflow](../docs/first-import.md) after local setup. Productive-data
migration, a stable release and qualification of later data classes are not claimed.

## Prepare and start

1. Build the current checkout with `make container-build`, or set `GITFERRY_IMAGE` to an image
   reference that includes an immutable digest.
2. Copy `.env.example` to an operator-owned `.env` outside version control.
3. Create the credential directory and the two token files named by the environment file. Give
   credential files mode `0600` and make them readable by the container UID `10001` (on Linux,
   assign ownership to `10001:10001`; the directory needs traversal permission for that UID).
   The original `source_api_token` and `target_api_token` aliases remain Compose secrets.
   Additional files in `GITFERRY_CREDENTIAL_DIR` are read-only local aliases: for example, a file
   named `comment-author` supplies that explicitly bound author's token. Never put token values
   in `.env`, configuration exports or SQLite. Adding another author file does not require editing
   the Compose file. A technical administrator token is not a substitute for an author token.
4. Create the CA directory. Add only the internal CA certificates required by local connection
   profiles. TLS verification remains enabled.
5. Run `docker compose --env-file .env -f deploy/compose.yaml up -d --wait`.

The service runs as UID/GID `10001`, with all Linux capabilities removed, a read-only root
filesystem, no Docker socket, and no privileged mode. Confirm readiness at `/readyz`; `/healthz` is
only the process liveness endpoint.

## Bootstrap and recovery

The running Compose service is non-root. Keep the operator password in a local protected file and
stream it to `docker compose exec`; the password is never placed in a command argument or
environment variable. The temporary file inside the container is removed by a shell trap:

```sh
password_file=/operator-owned/path/gitferry-admin-password
admin_username=operator.admin
test -f "$password_file"
test "$(stat -f '%Lp' "$password_file" 2>/dev/null || stat -c '%a' "$password_file")" = 600

docker compose --env-file .env -f deploy/compose.yaml exec -T gitferry sh -ceu '
  temporary=$(mktemp /tmp/gitferry-admin-password.XXXXXX)
  cleanup() { rm -f "$temporary"; }
  trap cleanup EXIT
  chmod 600 "$temporary"
  cat >"$temporary"
  gitferry admin bootstrap --database "$GITFERRY_STATE_DB" \
    --username "$1" --password-file "$temporary"
' sh "$admin_username" <"$password_file"
```

For recovery, use the same pattern and replace only the final CLI command with:

```text
gitferry admin recover --database "$GITFERRY_STATE_DB" --password-file "$temporary"
```

Recovery revokes all existing sessions. MFA, SSO and multi-administrator management are not
implemented. Terminate TLS in front of the service so secure cookies are protected in deployment.
Configure and approve connections in the UI, then follow the first-import guide. The profile and
author-reference fields contain filenames/aliases only. Missing or unreadable files and CAs block
the prerequisite check; they never trigger a fallback to another account.

## Persistent storage and replacement

The `gitferry-data` volume contains all three runtime areas:

- `state/gitferry.sqlite3` plus SQLite `-wal` and `-shm` companion files;
- `packages/` for portable transfer input/output;
- `work/` for bounded server-side working data.

Do not place the database file and its WAL/SHM files on different mounts. The storage must provide
local filesystem locking and atomic rename behavior suitable for SQLite WAL. Network filesystems
are unsupported unless separately qualified. GitFerry refuses relative, overlapping, symlinked,
overly permissive, or unwritable managed paths.

Container replacement is safe when the same volume is retained:

```text
docker compose --env-file .env -f deploy/compose.yaml stop gitferry
docker compose --env-file .env -f deploy/compose.yaml rm -f gitferry
docker compose --env-file .env -f deploy/compose.yaml up -d --wait gitferry
```

Startup runs the ordered SQLite migrations before readiness becomes healthy. A corrupt database or
an unknown newer schema stops the container with a non-zero result; GitFerry does not replace it
with an empty database.

## Backup boundary

The automated backup/restore workflow belongs to milestone 0.6.0. Until then, take a filesystem
copy only while the GitFerry container is stopped, and copy the complete `gitferry-data` volume.
Copying only the main SQLite file while the service is live can omit committed WAL data. A backup
is not accepted until it has been restored and checked by the later backup/restore qualification.
