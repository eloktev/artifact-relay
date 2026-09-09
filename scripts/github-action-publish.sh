#!/bin/sh
set -eu

: "${INPUT_ARTIFACT_PATH:?artifact-path input must be set.}"
: "${ARTIFACT_RELAY_BASE_URL:?relay-url input must be set.}"
: "${ARTIFACT_RELAY_API_TOKEN:?ARTIFACT_RELAY_API_TOKEN must be set in the step environment.}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set by GitHub Actions.}"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
set --
if [ -n "${INPUT_TITLE:-}" ]; then
    set -- "$@" --title "$INPUT_TITLE"
fi
if [ -n "${INPUT_SUMMARY:-}" ]; then
    set -- "$@" --summary "$INPUT_SUMMARY"
fi
if [ -n "${INPUT_FORMAT:-}" ]; then
    set -- "$@" --format "$INPUT_FORMAT"
fi
set -- "$@" --expires-in-days "${INPUT_EXPIRES_IN_DAYS:-30}" "$INPUT_ARTIFACT_PATH"

artifact_url=$("$script_dir/publish-file.sh" "$@")
printf 'artifact-url=%s\n' "$artifact_url" >>"$GITHUB_OUTPUT"
