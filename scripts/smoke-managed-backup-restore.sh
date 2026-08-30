#!/usr/bin/env bash
# Real managed backup/restore regression using the production three-layer Compose stack.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

command -v docker >/dev/null 2>&1 || { printf 'docker is required\n' >&2; exit 1; }
docker info >/dev/null 2>&1 || { printf 'a running Docker daemon is required\n' >&2; exit 1; }

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/artifact-relay-managed-backup-restore.XXXXXX")"
ARTIFACT_RELAY_PROJECT="artifact-relay-managed-backup-restore-$$"
ARTIFACT_RELAY_TENANT_ENV="$TMP_DIR/tenant.env"
IMAGE_TAG="ghcr.io/eloktev/artifact-relay:managed-backup-restore-$$"
BACKUP_DIR="$TMP_DIR/backup"
PORT="$((20000 + $$ % 20000))"
COMPOSE_ARGS=(
  --env-file "$ARTIFACT_RELAY_TENANT_ENV"
  --project-name "$ARTIFACT_RELAY_PROJECT"
  -f docker-compose.yml
  -f deploy/compose.ghcr.yml
  -f deploy/compose.managed.yml
)
export ARTIFACT_RELAY_PROJECT ARTIFACT_RELAY_TENANT_ENV

cleanup() {
  if [[ -n "${ARTIFACT_RELAY_DIGEST:-}" && -f "$ARTIFACT_RELAY_TENANT_ENV" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" down -v --remove-orphans --timeout 10 >/dev/null 2>&1 || true
  fi
  docker image rm "$IMAGE_TAG" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

printf 'Building managed smoke image...\n'
docker build --quiet -t "$IMAGE_TAG" . >/dev/null
IMAGE_ID="$(docker image inspect "$IMAGE_TAG" --format '{{.Id}}')"
ARTIFACT_RELAY_DIGEST="${IMAGE_ID#sha256:}"
export ARTIFACT_RELAY_DIGEST
HASH="$(docker run --rm "$IMAGE_TAG" python -c \
  'from artifact_relay.hashing import hash_password; print(hash_password("managed-smoke-password"))')"

{
  printf 'ARTIFACT_API_TOKEN=%s\n' 'managed-smoke-token-0123456789'
  printf "VIEW_PASSWORD_HASH='%s'\n" "$HASH"
  printf 'SESSION_SECRET_KEY=%s\n' 'managed-smoke-session-secret-0123456789abcdef'
  printf 'BASE_URL=%s\n' 'https://tenant.example.com'
  printf 'ARTIFACT_RELAY_PORT=%s\n' "$PORT"
  printf 'JANITOR_INTERVAL_SECONDS=%s\n' '60'
  printf 'SMOKE_RUNTIME_SENTINEL=%s\n' 'must-survive-backup-and-restore'
} >"$ARTIFACT_RELAY_TENANT_ENV"
chmod 0600 "$ARTIFACT_RELAY_TENANT_ENV"

python deploy/validate_managed_deployment.py "$ARTIFACT_RELAY_DIGEST" "$ARTIFACT_RELAY_TENANT_ENV"
docker compose "${COMPOSE_ARGS[@]}" up -d --wait --wait-timeout 120 app >/dev/null

container_id() {
  docker compose "${COMPOSE_ARGS[@]}" ps -q app
}
assert_runtime_unchanged() {
  local container current_image current_env
  container="$(container_id)"
  current_image="$(docker inspect "$container" --format '{{.Image}}')"
  current_env="$(docker inspect "$container" --format '{{json .Config.Env}}')"
  [[ "$current_image" == "$ORIGINAL_IMAGE" ]] || {
    printf 'managed image changed: expected %s, got %s\n' "$ORIGINAL_IMAGE" "$current_image" >&2
    return 1
  }
  [[ "$current_env" == "$ORIGINAL_ENV" ]] || {
    printf 'managed runtime environment changed\n' >&2
    return 1
  }
}

CONTAINER="$(container_id)"
ORIGINAL_IMAGE="$(docker inspect "$CONTAINER" --format '{{.Image}}')"
ORIGINAL_ENV="$(docker inspect "$CONTAINER" --format '{{json .Config.Env}}')"
[[ "$ORIGINAL_IMAGE" == "$IMAGE_ID" ]]
docker exec "$CONTAINER" sh -c \
  'mkdir -p /data/artifacts/managed-smoke && printf before-backup >/data/artifacts/managed-smoke/state'

MANAGED=1 "$ROOT/scripts/backup.sh" "$BACKUP_DIR"
assert_runtime_unchanged
[[ "$(docker exec "$(container_id)" cat /data/artifacts/managed-smoke/state)" == "before-backup" ]]

docker exec "$(container_id)" sh -c 'printf after-backup >/data/artifacts/managed-smoke/state'
MANAGED=1 "$ROOT/scripts/restore.sh" "$BACKUP_DIR/artifact-relay-data.tar.gz" --yes
assert_runtime_unchanged
[[ "$(docker exec "$(container_id)" cat /data/artifacts/managed-smoke/state)" == "before-backup" ]]

printf 'managed backup/restore smoke passed\n'
