#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:-}"
[[ -n "$ARCHIVE" && -f "$ARCHIVE" ]] || {
  printf 'Usage: %s PATH/artifact-relay-data.tar.gz [--yes]\n' "$0" >&2
  exit 2
}
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"
TOOL="$(cd "$(dirname "$0")" && pwd)/restore_archive.py"
if [[ "${2:-}" != "--yes" ]]; then
  printf 'Restore replaces all current artifact data. Type RESTORE to continue: ' >&2
  read -r answer
  [[ "$answer" == "RESTORE" ]] || { printf 'Cancelled.\n' >&2; exit 1; }
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/compose-args.sh
source "$SCRIPT_DIR/compose-args.sh"
configure_compose_args

run_restore_tool() {
  docker compose "${COMPOSE_ARGS[@]}" run --rm --no-deps \
    -v "$ARCHIVE:/restore/data.tar.gz:ro" \
    -v "$TOOL:/restore/restore_archive.py:ro" \
    --entrypoint python app /restore/restore_archive.py "$@"
}
restart() {
  docker compose "${COMPOSE_ARGS[@]}" up -d --wait --wait-timeout 120 app >/dev/null
}
stop_app() {
  docker compose "${COMPOSE_ARGS[@]}" stop app >/dev/null
}

# Complete archive/schema validation and reject unresolved recovery state before downtime.
run_restore_tool validate /restore/data.tar.gz
run_restore_tool ready /data

phase=before_stop
recover_on_error() {
  status=$?
  trap - EXIT
  case "$phase" in
    stopped)
      # apply either never started a transaction or left retry-safe recovery state.
      if run_restore_tool rollback /data; then
        restart || printf 'Original data was recovered, but the service is not healthy.\n' >&2
      else
        printf 'Rollback is incomplete; service remains stopped and recovery data is retained.\n' >&2
      fi
      ;;
    pending)
      # A restored container may still be running but unhealthy. Rollback is forbidden unless
      # shutdown succeeds, because replacing a live SQLite/file tree can mix generations.
      if ! stop_app; then
        printf 'Rollback skipped because application shutdown failed; recovery data is retained.\n' >&2
      elif run_restore_tool rollback /data; then
        restart || printf 'Original data was recovered, but the service is not healthy.\n' >&2
      else
        printf 'Rollback is incomplete; service remains stopped and recovery data is retained.\n' >&2
      fi
      ;;
    healthy)
      # The restored generation already passed health. A commit-boundary error must never mix
      # generations by rolling it back; unresolved recovery state blocks the next restore.
      printf 'Restored service is healthy, but recovery cleanup needs operator attention.\n' >&2
      ;;
  esac
  exit "$status"
}
trap recover_on_error EXIT

stop_app
phase=stopped
run_restore_tool apply /restore/data.tar.gz /data
phase=pending
restart
phase=healthy
run_restore_tool commit /data
phase="done"
trap - EXIT
printf 'Restore complete. Health check passed; verify a known artifact.\n'
