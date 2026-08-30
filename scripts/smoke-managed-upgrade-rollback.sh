#!/bin/sh
set -eu

command -v docker >/dev/null 2>&1 || {
  printf 'docker is required\n' >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  printf 'a running Docker daemon is required\n' >&2
  exit 1
}

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/artifact-relay-managed-smoke.XXXXXX")"
PROJECT="artifact-relay-managed-smoke-$$"
PREVIOUS_TAG="$PROJECT:previous"
NEW_TAG="$PROJECT:new"
BASE_COMPOSE="$TMP_DIR/docker-compose.yml"
IMAGE_COMPOSE="$TMP_DIR/compose.image.yml"
MANAGED_COMPOSE="$TMP_DIR/compose.managed.yml"
PRE_UPGRADE_BACKUP="$TMP_DIR/pre-upgrade-backup"

cleanup() {
  ARTIFACT_RELAY_DIGEST="${PREVIOUS_ARTIFACT_RELAY_DIGEST:-0000000000000000000000000000000000000000000000000000000000000000}" docker compose -p "$PROJECT" -f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE" down --remove-orphans >/dev/null 2>&1 || true
  docker image rm "$PREVIOUS_TAG" "$NEW_TAG" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

cat >"$TMP_DIR/Dockerfile" <<'EOF'
FROM python:3.12-alpine
ARG RELEASE
RUN printf '%s\n' "$RELEASE" >/release
HEALTHCHECK --interval=1s --timeout=2s --retries=10 CMD python -c "from pathlib import Path; assert Path('/release').read_text().strip()"
CMD ["python", "-m", "http.server", "8000"]
EOF

cat >"$BASE_COMPOSE" <<'EOF'
services:
  app:
    image: fixture-not-selected
EOF
cat >"$IMAGE_COMPOSE" <<'EOF'
services:
  app:
    image: "sha256:${ARTIFACT_RELAY_DIGEST:?Set ARTIFACT_RELAY_DIGEST}"
EOF
cat >"$MANAGED_COMPOSE" <<'EOF'
services:
  app:
    environment:
      MANAGED_FIXTURE: "true"
EOF

docker build --quiet --build-arg RELEASE=previous -t "$PREVIOUS_TAG" "$TMP_DIR" >/dev/null
docker build --quiet --build-arg RELEASE=new -t "$NEW_TAG" "$TMP_DIR" >/dev/null
PREVIOUS_ARTIFACT_RELAY_DIGEST="$(docker image inspect "$PREVIOUS_TAG" --format '{{.Id}}')"
PREVIOUS_ARTIFACT_RELAY_DIGEST="${PREVIOUS_ARTIFACT_RELAY_DIGEST#sha256:}"
NEW_ARTIFACT_RELAY_DIGEST="$(docker image inspect "$NEW_TAG" --format '{{.Id}}')"
NEW_ARTIFACT_RELAY_DIGEST="${NEW_ARTIFACT_RELAY_DIGEST#sha256:}"

ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST" docker compose -p "$PROJECT" -f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE" up -d --wait --wait-timeout 120 app >/dev/null
ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST" docker compose -p "$PROJECT" -f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE" exec -T app sh -c 'test "$(cat /release)" = previous'

mkdir -m 0700 "$PRE_UPGRADE_BACKUP"
printf '%s\n' "$PREVIOUS_ARTIFACT_RELAY_DIGEST" >"$PRE_UPGRADE_BACKUP/previous-digest"
printf '%s\n' "$NEW_ARTIFACT_RELAY_DIGEST" >"$PRE_UPGRADE_BACKUP/target-digest"

ARTIFACT_RELAY_DIGEST="$NEW_ARTIFACT_RELAY_DIGEST" docker compose -p "$PROJECT" -f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE" up -d --wait --wait-timeout 120 app >/dev/null
ARTIFACT_RELAY_DIGEST="$NEW_ARTIFACT_RELAY_DIGEST" docker compose -p "$PROJECT" -f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE" exec -T app sh -c 'test "$(cat /release)" = new'

test "$(cat "$PRE_UPGRADE_BACKUP/previous-digest")" = "$PREVIOUS_ARTIFACT_RELAY_DIGEST"
test "$(cat "$PRE_UPGRADE_BACKUP/target-digest")" = "$NEW_ARTIFACT_RELAY_DIGEST"
ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST" docker compose -p "$PROJECT" -f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE" up -d --wait --wait-timeout 120 app >/dev/null
ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST" docker compose -p "$PROJECT" -f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE" exec -T app sh -c 'test "$(cat /release)" = previous'

printf 'managed upgrade/rollback smoke passed\n'
