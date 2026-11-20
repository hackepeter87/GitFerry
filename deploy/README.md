# OCI deployment foundation

This Compose example exercises the accepted local runtime boundary and packages the same-origin
administrator frontend. It does not complete a transfer workflow or claim productive-data
migration.

## Prepare and start

1. Build the current checkout with `make container-build`, or set `GITFERRY_IMAGE` to an image
   reference that includes an immutable digest.
2. Copy `.env.example` to an operator-owned `.env` outside version control.
3. Create the two token files named by the environment file with mode `0600`. They are mounted as
   Compose secrets and are not embedded in the image or SQLite. The current runtime only preserves
   these references; connection-profile execution is delivered separately.
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

Recovery revokes all existing sessions. MFA, SSO, multi-administrator management, and a general
transfer UI are not implemented. Terminate TLS in front of the service so secure cookies are
protected in deployment.

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
