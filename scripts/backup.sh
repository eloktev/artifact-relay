#!/usr/bin/env bash
set -euo pipefail

DEST="${1:-backup-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
ARCHIVE="artifact-relay-data.tar.gz"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/compose-args.sh
source "$SCRIPT_DIR/compose-args.sh"
configure_compose_args

running="$(docker compose "${COMPOSE_ARGS[@]}" ps --status running -q app)"
restart() {
  if [[ -n "$running" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" up -d --wait --wait-timeout 120 app >/dev/null
  fi
}
trap restart EXIT

# Stop writes so the SQLite database and artifact tree are one consistent point-in-time copy.
docker compose "${COMPOSE_ARGS[@]}" stop app >/dev/null
rm -f "$DEST/$ARCHIVE"
docker compose "${COMPOSE_ARGS[@]}" run --rm --no-deps \
  --user 0:0 -e "BACKUP_UID=$(id -u)" -e "BACKUP_GID=$(id -g)" \
  -v "$DEST:/backup" --entrypoint python app -c '
import os, tarfile
target = "/backup/artifact-relay-data.tar.gz"
with tarfile.open(target, "w:gz") as archive:
    for name in ("artifacts.db", "artifacts"):
        path = "/data/" + name
        if os.path.exists(path):
            archive.add(path, arcname=name, recursive=True)
os.chmod(target, 0o600)
os.chown(target, int(os.environ["BACKUP_UID"]), int(os.environ["BACKUP_GID"]))
'
printf 'Backup written to %s/%s (secrets in .env are not included).\n' "$DEST" "$ARCHIVE"
