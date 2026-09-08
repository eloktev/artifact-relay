#!/bin/sh
set -eu

usage() {
    cat >&2 <<'EOF'
Usage: scripts/publish-file.sh [options] FILE

Options:
  --title TITLE             Artifact title (defaults to the file name)
  --summary SUMMARY         Optional artifact summary
  --format markdown|html    Override format detection from the extension
  --expires-in-days DAYS    Retention in days; 0 means no expiry (default: 30)
  --help                    Show this help
EOF
}

fail() {
    printf '%s\n' "$1" >&2
    exit 2
}

need_value() {
    [ "$#" -ge 2 ] || fail "Missing value for $1."
}

title=
summary=
summary_set=0
format=
expires_in_days=30
file=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --title)
            need_value "$@"
            title=$2
            shift 2
            ;;
        --summary)
            need_value "$@"
            summary=$2
            summary_set=1
            shift 2
            ;;
        --format)
            need_value "$@"
            format=$2
            shift 2
            ;;
        --expires-in-days)
            need_value "$@"
            expires_in_days=$2
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            fail "Unknown option: $1"
            ;;
        *)
            [ -z "$file" ] || fail "Only one input file may be published."
            file=$1
            shift
            ;;
    esac
done

if [ "$#" -gt 0 ]; then
    [ -z "$file" ] || fail "Only one input file may be published."
    file=$1
    shift
fi
[ "$#" -eq 0 ] || fail "Only one input file may be published."

if [ -z "$file" ]; then
    usage
    exit 2
fi

[ -f "$file" ] && [ -r "$file" ] || fail "FILE must be an existing readable file."

case "$file" in
    *','*|*';'*|*'"'*|*'\'*)
        fail "FILE name must not contain commas, semicolons, quotes, or backslashes."
        ;;
esac

case "$format" in
    markdown|html)
        ;;
    "")
        case "$file" in
            *.[mM][dD]|*.[mM][aA][rR][kK][dD][oO][wW][nN]) format=markdown ;;
            *.[hH][tT][mM][lL]|*.[hH][tT][mM]) format=html ;;
            *) fail "Could not detect format; use --format markdown or --format html." ;;
        esac
        ;;
    *)
        fail "--format must be markdown or html."
        ;;
esac

case "$expires_in_days" in
    ""|*[!0-9]*) fail "--expires-in-days must be a non-negative integer." ;;
esac

if [ -z "$title" ]; then
    filename=${file##*/}
    title=${filename%.*}
fi
[ -n "$title" ] || fail "--title must not be empty."

: "${ARTIFACT_RELAY_API_TOKEN:?ARTIFACT_RELAY_API_TOKEN must be set in the environment.}"
base_url=${ARTIFACT_RELAY_BASE_URL:-http://localhost:8000}
base_url=${base_url%/}

case "$base_url" in
    http://*) authority=${base_url#http://} ;;
    https://*) authority=${base_url#https://} ;;
    *) fail "ARTIFACT_RELAY_BASE_URL must be an HTTP(S) origin." ;;
esac
case "$authority" in
    ""|*/*|*'?'*|*'#'*|*'@'*|*' '*|*'\t'*)
        fail "ARTIFACT_RELAY_BASE_URL must be an HTTP(S) origin without credentials, path, query, or fragment."
        ;;
esac

command -v curl >/dev/null 2>&1 || fail "Required command not found: curl"
command -v python3 >/dev/null 2>&1 || fail "Required command not found: python3"
command -v mktemp >/dev/null 2>&1 || fail "Required command not found: mktemp"

if ! ARTIFACT_RELAY_VALIDATION_BASE_URL=$base_url python3 -c '
import os
from ipaddress import ip_address
import string
import sys
from urllib.parse import urlsplit

try:
    raw = os.environ["ARTIFACT_RELAY_VALIDATION_BASE_URL"]
    parsed = urlsplit(raw)
    parsed.port
    hostname = parsed.hostname or ""
    is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            pass
    valid = (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path == ""
        and not parsed.query
        and not parsed.fragment
        and all(33 <= ord(char) <= 126 for char in raw)
        and all(char in string.ascii_letters + string.digits + ".-:[]" for char in parsed.netloc)
        and (parsed.scheme == "https" or is_loopback)
    )
except ValueError:
    valid = False
raise SystemExit(not valid)
'; then
    fail "ARTIFACT_RELAY_BASE_URL must be a valid origin and use HTTPS unless it addresses loopback."
fi

if ! python3 -c 'import os, sys; t = os.environ["ARTIFACT_RELAY_API_TOKEN"]; sys.exit(not all(32 <= ord(c) <= 126 for c in t))'; then
    fail "ARTIFACT_RELAY_API_TOKEN must contain only printable ASCII characters."
fi

umask 077
response_file=$(mktemp "${TMPDIR:-/tmp}/artifact-relay-response.XXXXXX") || exit 1
error_file=$(mktemp "${TMPDIR:-/tmp}/artifact-relay-error.XXXXXX") || {
    rm -f "$response_file"
    exit 1
}
trap 'rm -f "$response_file" "$error_file"' 0 HUP INT TERM

case "$format" in
    markdown) media_type=text/markdown ;;
    html) media_type=text/html ;;
esac

set -- \
    --fail \
    --silent \
    --show-error \
    --request POST \
    --form-string "title=$title" \
    --form-string "format=$format" \
    --form-string "expires_in_days=$expires_in_days"
if [ "$summary_set" -eq 1 ]; then
    set -- "$@" --form-string "summary=$summary"
fi
set -- "$@" \
    --form "content=@${file};type=${media_type}" \
    "${base_url}/api/artifacts" \
    --config -

if ! python3 -c 'import json, os; print("header = " + json.dumps("Authorization: Bearer " + os.environ["ARTIFACT_RELAY_API_TOKEN"]))' |
    curl "$@" >"$response_file" 2>"$error_file"; then
    printf 'Publish failed. Check the relay URL, token, and server logs.\n' >&2
    exit 1
fi

if ! python3 -c '
import json
import sys
from ipaddress import ip_address
from urllib.parse import urlsplit

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        url = json.load(stream)["url"]
    if not isinstance(url, str):
        raise ValueError
    parsed = urlsplit(url)
    parsed.port
    hostname = parsed.hostname or ""
    is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            pass
    artifact_id = parsed.path.removeprefix("/a/")
    valid = (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and bool(artifact_id)
        and "/" not in artifact_id
        and parsed.path == "/a/" + artifact_id
        and all(33 <= ord(char) <= 126 for char in url)
        and (parsed.scheme == "https" or is_loopback)
    )
    if not valid:
        raise ValueError
except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
    raise SystemExit(1)
print(url)
' "$response_file" 2>/dev/null; then
    printf 'Publish succeeded, but the response did not contain a valid artifact URL.\n' >&2
    exit 1
fi
