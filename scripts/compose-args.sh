#!/usr/bin/env bash

configure_compose_args() {
  COMPOSE_ARGS=(-f docker-compose.yml)

  if [[ "${MANAGED:-0}" == "1" ]]; then
    : "${ARTIFACT_RELAY_TENANT_ENV:?ARTIFACT_RELAY_TENANT_ENV is required when MANAGED=1}"
    : "${ARTIFACT_RELAY_PROJECT:?ARTIFACT_RELAY_PROJECT is required when MANAGED=1}"
    : "${ARTIFACT_RELAY_DIGEST:?ARTIFACT_RELAY_DIGEST is required when MANAGED=1}"
    [[ -f "$ARTIFACT_RELAY_TENANT_ENV" ]] || {
      printf 'ARTIFACT_RELAY_TENANT_ENV does not exist: %s\n' "$ARTIFACT_RELAY_TENANT_ENV" >&2
      return 2
    }
    [[ "$ARTIFACT_RELAY_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || {
      printf 'ARTIFACT_RELAY_PROJECT must contain only lowercase letters, digits, hyphens, and underscores\n' >&2
      return 2
    }
    [[ "$ARTIFACT_RELAY_DIGEST" =~ ^[0-9a-f]{64}$ ]] || {
      printf 'ARTIFACT_RELAY_DIGEST must be 64 lowercase hexadecimal characters\n' >&2
      return 2
    }
    COMPOSE_ARGS=(
      --env-file "$ARTIFACT_RELAY_TENANT_ENV"
      --project-name "$ARTIFACT_RELAY_PROJECT"
      -f docker-compose.yml
      -f deploy/compose.ghcr.yml
      -f deploy/compose.managed.yml
    )
    return
  fi

  if [[ -f deploy/compose.vps.yml && "${VPS:-0}" == "1" ]]; then
    COMPOSE_ARGS+=(-f deploy/compose.vps.yml)
  fi
}
