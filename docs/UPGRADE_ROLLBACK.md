# Upgrade and rollback

Use immutable image digests, never `latest`. An upgrade is not reproducible unless the exact
previous and new manifest-list digests and the backup made for that transition are recorded
together. Keep the pre-upgrade image and backup until rollback acceptance succeeds.

## Managed GHCR deployment

Run these commands from the exact public release checkout used to deploy the tenant. Keep the tenant
env file, project name, digest, and all three Compose layers identical for every `ps`, `stop`, `run`,
`up`, and rollback operation.

```sh
export ARTIFACT_RELAY_TENANT_ENV=/secure/tenant.env
export ARTIFACT_RELAY_PROJECT=artifact-relay-tenant
export PREVIOUS_ARTIFACT_RELAY_DIGEST='<currently deployed 64-hex manifest-list digest>'
export NEW_ARTIFACT_RELAY_DIGEST='<target 64-hex manifest-list digest>'
export PRE_UPGRADE_BACKUP="/secure/backups/pre-upgrade-${PREVIOUS_ARTIFACT_RELAY_DIGEST}-to-${NEW_ARTIFACT_RELAY_DIGEST}-$(date -u +%Y%m%dT%H%M%SZ)"

python deploy/validate_managed_deployment.py "$PREVIOUS_ARTIFACT_RELAY_DIGEST" "$ARTIFACT_RELAY_TENANT_ENV"
python deploy/validate_managed_deployment.py "$NEW_ARTIFACT_RELAY_DIGEST" "$ARTIFACT_RELAY_TENANT_ENV"

# Confirm and accept the exact previous deployment before associating its backup.
export ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST"
docker compose --env-file "$ARTIFACT_RELAY_TENANT_ENV" --project-name "$ARTIFACT_RELAY_PROJECT" -f docker-compose.yml -f deploy/compose.ghcr.yml -f deploy/compose.managed.yml up -d --wait --wait-timeout 120 app
curl --fail --silent --show-error --connect-timeout 5 --max-time 15 https://tenant.example.com/api/health

mkdir -m 0700 "$PRE_UPGRADE_BACKUP"
printf '%s\n' "$PREVIOUS_ARTIFACT_RELAY_DIGEST" >"$PRE_UPGRADE_BACKUP/previous-digest"
printf '%s\n' "$NEW_ARTIFACT_RELAY_DIGEST" >"$PRE_UPGRADE_BACKUP/target-digest"
printf '%s\n' "$ARTIFACT_RELAY_PROJECT" >"$PRE_UPGRADE_BACKUP/managed-project"
printf '%s\n' "$ARTIFACT_RELAY_TENANT_ENV" >"$PRE_UPGRADE_BACKUP/tenant-env"
MANAGED=1 ./scripts/backup.sh "$PRE_UPGRADE_BACKUP/data"

test "$(cat "$PRE_UPGRADE_BACKUP/previous-digest")" = "$PREVIOUS_ARTIFACT_RELAY_DIGEST"
test "$(cat "$PRE_UPGRADE_BACKUP/target-digest")" = "$NEW_ARTIFACT_RELAY_DIGEST"
test "$(cat "$PRE_UPGRADE_BACKUP/managed-project")" = "$ARTIFACT_RELAY_PROJECT"
test "$(cat "$PRE_UPGRADE_BACKUP/tenant-env")" = "$ARTIFACT_RELAY_TENANT_ENV"

export ARTIFACT_RELAY_DIGEST="$NEW_ARTIFACT_RELAY_DIGEST"
docker compose --env-file "$ARTIFACT_RELAY_TENANT_ENV" --project-name "$ARTIFACT_RELAY_PROJECT" -f docker-compose.yml -f deploy/compose.ghcr.yml -f deploy/compose.managed.yml pull app
docker compose --env-file "$ARTIFACT_RELAY_TENANT_ENV" --project-name "$ARTIFACT_RELAY_PROJECT" -f docker-compose.yml -f deploy/compose.ghcr.yml -f deploy/compose.managed.yml up -d --wait --wait-timeout 120 app
curl --fail --silent --show-error --connect-timeout 5 --max-time 15 https://tenant.example.com/api/health
```

The Compose wait is bounded at 120 seconds and each HTTP acceptance is bounded at 15 seconds.
After health succeeds, verify login, publish, byte-identical read, and deletion with a disposable
artifact before declaring the upgrade accepted.

`MANAGED=1` makes the backup and restore scripts fail closed unless
`ARTIFACT_RELAY_TENANT_ENV`, `ARTIFACT_RELAY_PROJECT`, and `ARTIFACT_RELAY_DIGEST` are all exported.
The scripts then reuse the exact managed Compose invocation for discovery, shutdown, helper
containers, restart, and rollback. Do not substitute `VPS=1`; that selects the source/VPS override,
not the managed GHCR stack.

## Managed rollback

A code rollback and a data rollback are separate decisions. Read both releases' notes before
restoring data. Roll the image back to the recorded previous digest with the recorded env file,
project name, and same three Compose layers:

```sh
test "$(cat "$PRE_UPGRADE_BACKUP/previous-digest")" = "$PREVIOUS_ARTIFACT_RELAY_DIGEST"
test "$(cat "$PRE_UPGRADE_BACKUP/managed-project")" = "$ARTIFACT_RELAY_PROJECT"
test "$(cat "$PRE_UPGRADE_BACKUP/tenant-env")" = "$ARTIFACT_RELAY_TENANT_ENV"
export ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST"
docker compose --env-file "$ARTIFACT_RELAY_TENANT_ENV" --project-name "$ARTIFACT_RELAY_PROJECT" -f docker-compose.yml -f deploy/compose.ghcr.yml -f deploy/compose.managed.yml up -d --wait --wait-timeout 120 app
curl --fail --silent --show-error --connect-timeout 5 --max-time 15 https://tenant.example.com/api/health
```

If the previous release cannot read data written by the new release, restore the backup associated
with that exact previous digest, then repeat the bounded Compose and HTTP acceptance:

```sh
MANAGED=1 ./scripts/restore.sh "$PRE_UPGRADE_BACKUP/data/artifact-relay-data.tar.gz"
```

Do not delete the newer volume contents, either image, or `PRE_UPGRADE_BACKUP` until rollback login,
publish, byte-identical read, and deletion acceptance succeeds.

## Local executable smokes

The upgrade/rollback smoke builds two harmless fixture images, addresses each by its exact local
image digest, and exercises initial deployment, upgrade, backup association, and rollback using
three Compose layers and the production `up -d --wait --wait-timeout 120 app` shape:

```sh
./scripts/smoke-managed-upgrade-rollback.sh
```

The managed backup/restore regression builds the real application image, runs the production base,
GHCR, and managed Compose layers, and invokes the real backup and restore scripts. It verifies the
exact image digest and full container runtime environment before and after both restarts, restores a
changed data marker, and removes its project, volume, image, and temporary secrets on exit:

```sh
./scripts/smoke-managed-backup-restore.sh
```

These smokes do not pull or publish Artifact Relay images and do not replace acceptance against the
exact published manifest-list digests.

## Source-build deployments

For source builds, check out an immutable signed release tag, leave the default pull policy at
`build`, run `docker compose ... build --pull app`, and use `up -d --wait --wait-timeout 120 app`.
Take a tested backup first. Retain the old source checkout, image, and backup until the same bounded
health and disposable-artifact acceptance succeeds.
