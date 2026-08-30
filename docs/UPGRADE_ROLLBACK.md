# Upgrade and rollback

Use immutable release tags such as `1.0.0`, never `latest`. Record the exact image digest from
`docker image inspect` in your change log. Take a tested backup before every upgrade.

## Upgrade

```sh
VPS=1 ./scripts/backup.sh /secure/backups/pre-upgrade-$(date -u +%FT%H%M%SZ)
export ARTIFACT_RELAY_IMAGE=registry.example/artifact-relay:1.1.0
docker compose -f docker-compose.yml -f deploy/compose.vps.yml pull app
docker compose -f docker-compose.yml -f deploy/compose.vps.yml up -d app
docker compose -f docker-compose.yml -f deploy/compose.vps.yml ps
curl -fsS https://artifacts.example.com/api/health
```

For source builds, check out an immutable signed release tag, leave the default pull policy at
`build`, and run `docker compose ... build --pull app` before `up -d`. Verify login, publishing,
viewing, and deletion with a disposable artifact. Retain the old image and pre-upgrade backup.

## Rollback

A code rollback and a data rollback are separate decisions. Read release notes for schema changes.
If the new release did not change persisted data compatibly:

```sh
export ARTIFACT_RELAY_IMAGE=registry.example/artifact-relay:1.0.0
docker compose -f docker-compose.yml -f deploy/compose.vps.yml up -d app
curl -fsS https://artifacts.example.com/api/health
```

If the older release cannot read data written by the newer one, restore the matching pre-upgrade
archive first:

```sh
VPS=1 ./scripts/restore.sh /secure/backups/pre-upgrade-.../artifact-relay-data.tar.gz
```

Do not delete the newer volume contents or image until the rollback is verified. If using an
image tag from a registry, compare its digest with the digest recorded before deployment; tags
can be moved even when your operational policy treats them as immutable.
