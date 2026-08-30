# Backup and restore

All persistent application data is in the Compose volume `artifact-data`. Secrets are in
`.env` and are intentionally excluded from data backups.

## Backup

From the repository root:

```sh
./scripts/backup.sh
# VPS override:
VPS=1 ./scripts/backup.sh /secure/backups/artifact-relay-$(date -u +%F)
# Managed GHCR deployment (all managed variables must be exported):
MANAGED=1 ./scripts/backup.sh /secure/backups/artifact-relay-$(date -u +%F)
```

The script stops the application briefly so the SQLite database and artifact files represent
the same point in time, writes `artifact-relay-data.tar.gz`, sets mode `0600`, and restarts
the service if it was running. Store the archive encrypted. Separately store `.env` in a
password manager or secret backup; without the same session key existing sessions are logged
out, and without the password hash or API token access must be reconfigured.

Validate and inventory a backup without extracting it:

```sh
python3 -m tarfile -l /secure/backups/.../artifact-relay-data.tar.gz
```

## Restore

Restore only into a compatible application version. Back up current data first, then:

```sh
./scripts/restore.sh /secure/backups/.../artifact-relay-data.tar.gz
# Non-interactive automation after an external approval gate:
VPS=1 ./scripts/restore.sh /secure/backups/.../artifact-relay-data.tar.gz --yes
# Managed GHCR deployment after an external approval gate:
MANAGED=1 ./scripts/restore.sh /secure/backups/.../artifact-relay-data.tar.gz --yes
```

For managed operations, export `ARTIFACT_RELAY_TENANT_ENV`, `ARTIFACT_RELAY_PROJECT`, and the exact
`ARTIFACT_RELAY_DIGEST` first. `MANAGED=1` fails closed if any value is absent and uses the tenant
env file, project name, digest, and base + GHCR + managed Compose layers for every operation. See
`UPGRADE_ROLLBACK.md` for the complete recorded-digest procedure.

Before stopping the service, the script rejects traversal, links, unexpected members, corrupt
SQLite, an incompatible database schema, and unresolved recovery state. It then extracts into an
isolated staging tree and swaps the validated database/artifacts into place while retaining the
previous tree until the restored service passes its health check. If the swap or health check
fails, it first confirms all application access has stopped, then performs a resumable rollback and
starts the original service only after recovery completes. If shutdown cannot be confirmed,
rollback is skipped and recovery state is retained for operator intervention. Successful startup
atomically renames the recovery tree out of the rollback namespace before best-effort deletion,
preventing partial cleanup from mixing generations. Health waits are bounded to 120 seconds; if
preflight fails, live data and the running service are untouched.
Verify immediately:

```sh
curl -fsS http://localhost:8000/api/health
# For VPS: curl -fsS https://artifacts.example.com/api/health
```

Then log in and open a known artifact. Keep the pre-restore backup until verification completes.
