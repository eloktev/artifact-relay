# VPS deployment with Caddy

This example runs one application replica behind Caddy on the same Linux VPS.
Docker publishes the application only on `127.0.0.1`; Caddy owns ports 80/443.

## Prerequisites

- Docker Engine with Compose v2
- Caddy 2 installed on the host
- DNS A/AAAA records for your hostname pointing to the VPS
- A cloned, versioned checkout or an immutable container image

## Configure

1. Select the exact application image **before** bootstrap hashes the viewer password.

   For a versioned source checkout:

   ```sh
   docker build -t artifact-relay:1.0.0 .
   export ARTIFACT_RELAY_IMAGE=artifact-relay:1.0.0
   ```

   Or pull an immutable registry image:

   ```sh
   export ARTIFACT_RELAY_IMAGE=registry.example/artifact-relay:1.0.0
   export ARTIFACT_RELAY_PULL_POLICY=missing
   docker pull "$ARTIFACT_RELAY_IMAGE"
   ```

   A digest (`image@sha256:...`) gives stronger immutability.

2. Run `./scripts/bootstrap.sh` to create `.env`, then edit only these values:

   ```dotenv
   BASE_URL=https://artifacts.example.com
   COOKIE_SECURE=true
   SHARE_LINKS_ENABLED=true
   FORWARDED_ALLOW_IPS=127.0.0.1
   ```

   The Compose override enforces the first three values as an additional guard. Keep `.env`
   mode `0600`. Do not put plaintext passwords in it.

3. Replace `artifacts.example.com` in `deploy/compose.vps.yml` and
   `deploy/Caddyfile.example` with your real hostname.
4. Install the Caddy example as your site configuration and validate it:

   ```sh
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

5. Start and verify:

   ```sh
   docker compose -f docker-compose.yml -f deploy/compose.vps.yml config
   docker compose -f docker-compose.yml -f deploy/compose.vps.yml up -d
   docker compose -f docker-compose.yml -f deploy/compose.vps.yml ps
   curl -fsS https://artifacts.example.com/api/health
   ```

Run exactly one replica: storage is SQLite and rate limiting is process-local. The data volume,
`.env`, and backups contain private material. Restrict host access and include them in your
normal encrypted backup policy. Never expose container port 8000 on `0.0.0.0`.

See `BACKUP_RESTORE.md` and `UPGRADE_ROLLBACK.md` before the first upgrade.
