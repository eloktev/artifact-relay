#!/usr/bin/env bash
# End-to-end smoke test against a real container: publish -> preview -> login -> view ->
# assets -> source -> restart -> delete. Exits non-zero on the first failed expectation.
#
#   ./scripts/smoke.sh [image] [port]
set -euo pipefail

IMAGE="${1:-artifact-relay:local}"
PORT="${2:-18080}"
NAME="ap-smoke-$$"
VOLUME="ap-smoke-vol-$$"
BASE="http://localhost:${PORT}"
JAR="$(mktemp -d)/cookies.txt"
WORK="$(mktemp -d)"

# Smoke-test-only credentials, generated per run. Never reused, never committed.
TOKEN="smoke-token-$(openssl rand -hex 12)"
PASSWORD="smoke-password-$(openssl rand -hex 8)"
SESSION_KEY="$(openssl rand -base64 48 | tr -d '\n')"

pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  rm -rf "$WORK" "$(dirname "$JAR")"
}
trap cleanup EXIT

step "hash the viewer password inside the image"
HASH="$(docker run --rm "$IMAGE" python -c \
  "from artifact_relay.hashing import hash_password;import sys;print(hash_password(sys.argv[1]))" \
  "$PASSWORD")"
[[ "$HASH" == \$argon2id\$* ]] || fail "hashing helper did not produce an argon2id hash"
pass "argon2id hash generated"

step "start the container"
docker volume create "$VOLUME" >/dev/null
docker run -d --name "$NAME" -p "${PORT}:8000" \
  -e ARTIFACT_API_TOKEN="$TOKEN" \
  -e VIEW_PASSWORD_HASH="$HASH" \
  -e SESSION_SECRET_KEY="$SESSION_KEY" \
  -e BASE_URL="$BASE" \
  -e COOKIE_SECURE=false \
  -e JANITOR_INTERVAL_SECONDS=60 \
  -e LOGIN_MAX_ATTEMPTS=3 \
  -v "$VOLUME:/data" \
  "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  curl -fsS "$BASE/api/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$BASE/api/health" | grep -q '"status":"ok"' || fail "health endpoint never came up"
pass "GET /api/health -> {\"status\":\"ok\"}"

step "wait for the container HEALTHCHECK to report healthy"
for _ in $(seq 1 60); do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$NAME")"
  [[ "$STATUS" == "healthy" ]] && break
  sleep 1
done
[[ "$STATUS" == "healthy" ]] || fail "container health is '$STATUS'"
pass "docker healthcheck -> healthy"

step "runtime identity"
[[ "$(docker exec "$NAME" id -u)" == "10001" ]] || fail "container is not running as uid 10001"
docker exec "$NAME" sh -c 'test -w /data' || fail "/data is not writable by the runtime user"
pass "uid 10001, /data writable"

step "auth boundary"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/api/artifacts")"
[[ "$CODE" == "401" ]] || fail "unauthenticated publish returned $CODE, expected 401"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/api/artifacts" \
        -H "Authorization: Bearer ${TOKEN}x")"
[[ "$CODE" == "401" ]] || fail "wrong token returned $CODE, expected 401"
pass "publish rejects missing and wrong bearer tokens"

step "publish a markdown artifact with an asset"
cat > "$WORK/report.md" <<'MD'
# Отчёт о нагрузочном тесте

СЕКРЕТНОЕ-ТЕЛО-ДОКУМЕНТА

## Результаты

| Сервис | p99 |
| --- | ---: |
| api | 240 мс |

- [x] Выкатили
- [ ] Переключили трафик

![Диаграмма](chart.png)
![Она же](assets/chart.png)
![Внешняя](https://cdn.example.com/x.png)

```python
def total(rows: list[int]) -> int:
    return sum(rows)
```

```mermaid
graph TD
  A[Клиент] --> B[API]
```

<script>alert('xss')</script>
<img src=x onerror="alert('xss')">
MD
printf '\x89PNG\r\n\x1a\n' > "$WORK/chart.png"

PUBLISH="$(curl -sS -X POST "$BASE/api/artifacts" \
  -H "Authorization: Bearer $TOKEN" \
  -F 'title=Отчёт о нагрузочном тесте' \
  -F 'summary=p99 вырос на 40 мс' \
  -F 'format=markdown' \
  -F 'expires_in_days=30' \
  -F "content=@$WORK/report.md;type=text/markdown" \
  -F "assets=@$WORK/chart.png")"
ID="$(printf '%s' "$PUBLISH" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
URL="$(printf '%s' "$PUBLISH" | python3 -c 'import json,sys;print(json.load(sys.stdin)["url"])')"
[[ -n "$ID" ]] || fail "publish returned no id"
[[ "$URL" == "$BASE/a/$ID" ]] || fail "url '$URL' does not match the configured BASE_URL"
[[ ${#ID} -ge 22 ]] || fail "artifact id '$ID' is too short to be opaque"
pass "published $ID"

step "anonymous request: Open Graph metadata, no body"
ANON="$(curl -sS "$BASE/a/$ID")"
grep -q 'property="og:title" content="Отчёт о нагрузочном тесте"' <<<"$ANON" || fail "og:title missing"
grep -q 'property="og:description" content="p99 вырос на 40 мс"' <<<"$ANON" || fail "og:description missing"
grep -q "property=\"og:image\" content=\"$BASE/a/$ID/og.png\"" <<<"$ANON" || fail "og:image missing"
grep -q 'content="noindex, nofollow, noarchive"' <<<"$ANON" || fail "robots meta missing"
grep -q 'type="password"' <<<"$ANON" || fail "login form missing from the shell"
grep -q 'СЕКРЕТНОЕ-ТЕЛО-ДОКУМЕНТА' <<<"$ANON" && fail "private body leaked to an anonymous visitor"
pass "shell has OG tags + login form, body withheld"

step "Open Graph card is public and is a real PNG"
curl -fsS -o "$WORK/og.png" "$BASE/a/$ID/og.png"
python3 - "$WORK/og.png" <<'PY' || exit 1
import sys
blob = open(sys.argv[1], "rb").read()
assert blob[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
assert b"\xd0\xa1\xd0\x95\xd0\x9a\xd0\xa0\xd0\x95\xd0\xa2" not in blob, "secret bytes in card"
print(f"  ok   og.png is {len(blob)} bytes of PNG")
PY

step "protected sub-resources refuse an anonymous caller"
for path in "source" "assets/chart.png"; do
  CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/a/$ID/$path")"
  [[ "$CODE" == "403" || "$CODE" == "404" ]] || fail "/a/\$ID/$path returned $CODE anonymously"
done
pass "raw/source/assets are 403 or 404 without a session"

step "login"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/login" \
        --data-urlencode "password=wrong-password" --data-urlencode "next=/a/$ID")"
[[ "$CODE" == "401" ]] || fail "wrong password returned $CODE, expected 401"

LOC="$(curl -sS -o /dev/null -D - -c "$JAR" -X POST "$BASE/login" \
       --data-urlencode "password=$PASSWORD" --data-urlencode "next=/a/$ID" \
       | tr -d '\r' | awk '/^[Ll]ocation:/{print $2}')"
[[ "$LOC" == "/a/$ID" ]] || fail "login redirected to '$LOC', expected /a/$ID"
grep -qi 'ap_session' "$JAR" || fail "no session cookie was set"
pass "wrong password -> 401; correct password -> 303 back to the artifact"

step "open redirect is refused"
LOC="$(curl -sS -o /dev/null -D - -X POST "$BASE/login" \
       --data-urlencode "password=$PASSWORD" --data-urlencode "next=https://evil.example/x" \
       | tr -d '\r' | awk '/^[Ll]ocation:/{print $2}')"
[[ "$LOC" == "/" ]] || fail "off-site next was honoured: '$LOC'"
pass "external next falls back to /"

step "view the rendered artifact"
PAGE="$(curl -sS -b "$JAR" "$BASE/a/$ID")"
grep -q 'СЕКРЕТНОЕ-ТЕЛО-ДОКУМЕНТА' <<<"$PAGE" || fail "body missing for an authenticated viewer"
grep -q '<table' <<<"$PAGE" || fail "markdown table not rendered"
grep -q 'type="checkbox"' <<<"$PAGE" || fail "task list not rendered"
grep -q '<span class="k">def</span>' <<<"$PAGE" || fail "python was not syntax highlighted"
grep -q '<div class="mermaid">' <<<"$PAGE" || fail "mermaid block not rendered"
grep -q 'src="/static/js/mermaid.min.js"' <<<"$PAGE" || fail "mermaid bundle not loaded locally"
grep -qE '<script[^>]+src="https?://' <<<"$PAGE" && fail "page references a remote script"
grep -q 'onerror' <<<"$PAGE" && fail "event handler survived sanitisation"
grep -q '<script>alert' <<<"$PAGE" && fail "injected script survived sanitisation"
grep -q 'href="#h-' <<<"$PAGE" || fail "table of contents missing"
pass "markdown rendered, sanitised, TOC present, mermaid bundled locally"

step "artifact-relative markdown asset references resolve"
# Both `chart.png` and `assets/chart.png` must land on the artifact's own assets directory;
# the page lives at /a/<id>, so unrewritten they would resolve to /a/chart.png.
COUNT="$(grep -o "src=\"/a/$ID/assets/chart.png\"" <<<"$PAGE" | wc -l | tr -d ' ')"
[[ "$COUNT" == "2" ]] || fail "expected both relative spellings rewritten, found $COUNT"
grep -q 'src="chart.png"' <<<"$PAGE" && fail "a relative reference was left unresolved"
grep -q 'src="assets/chart.png"' <<<"$PAGE" && fail "a relative reference was left unresolved"
grep -q 'src="https://cdn.example.com/x.png"' <<<"$PAGE" || fail "an external image was rewritten"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/a/$ID/assets/chart.png")"
[[ "$CODE" == "200" ]] || fail "the rewritten asset URL returned $CODE"
pass "chart.png and assets/chart.png both resolve to /a/\$ID/assets/chart.png and serve 200"

step "mermaid bundle is served from this origin"
curl -fsS -o "$WORK/mermaid.js" "$BASE/static/js/mermaid.min.js"
[[ "$(wc -c < "$WORK/mermaid.js")" -gt 500000 ]] || fail "mermaid bundle looks truncated"
grep -q 'new Function' "$WORK/mermaid.js" && fail "bundle needs unsafe-eval"
pass "mermaid.min.js served locally, $(wc -c < "$WORK/mermaid.js") bytes, no eval"

step "source download and assets"
HDRS="$(curl -sS -D - -o "$WORK/source.md" -b "$JAR" "$BASE/a/$ID/source" | tr -d '\r')"
grep -qi '^content-disposition: attachment' <<<"$HDRS" || fail "source is not an attachment"
diff -q "$WORK/report.md" "$WORK/source.md" >/dev/null || fail "source download differs from what was published"
CT="$(curl -sS -o /dev/null -w '%{content_type}' -b "$JAR" "$BASE/a/$ID/assets/chart.png")"
[[ "$CT" == "image/png" ]] || fail "asset content-type is '$CT', expected image/png"
pass "source is byte-identical and downloads; asset served as image/png"

step "path traversal"
for hostile in "%2e%2e" "%2e%2e%2f%2e%2e%2fetc%2fpasswd" "..%2f..%2fetc%2fpasswd"; do
  CODE="$(curl -sS -o "$WORK/trav.out" -w '%{http_code}' -b "$JAR" "$BASE/a/$ID/assets/$hostile")"
  [[ "$CODE" == "404" || "$CODE" == "400" ]] || fail "traversal '$hostile' returned $CODE"
  grep -q 'root:' "$WORK/trav.out" && fail "traversal '$hostile' read a system file"
done
pass "encoded traversal attempts return 404/400 and leak nothing"

step "publish a standalone HTML artifact and check the sandbox"
cat > "$WORK/info.html" <<'HTML'
<!doctype html><html lang="ru"><head><meta charset="utf-8">
<style>body{font-family:system-ui;padding:2rem}</style></head>
<body><h1>ИНТЕРАКТИВНАЯ-ИНФОГРАФИКА</h1>
<script>document.body.dataset.ready = "1";</script></body></html>
HTML
HTML_ID="$(curl -sS -X POST "$BASE/api/artifacts" \
  -H "Authorization: Bearer $TOKEN" \
  -F 'title=Инфографика' -F 'format=html' \
  -F "content=@$WORK/info.html;type=text/html" \
  -F "assets=@$WORK/chart.png" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"

HTML_PAGE="$(curl -sS -b "$JAR" "$BASE/a/$HTML_ID")"
grep -q 'ИНТЕРАКТИВНАЯ-ИНФОГРАФИКА' <<<"$HTML_PAGE" && fail "artifact HTML was inlined into the viewer page"
grep -q 'sandbox="allow-scripts"' <<<"$HTML_PAGE" || fail "iframe is not sandboxed with allow-scripts only"
grep -q 'allow-same-origin' <<<"$HTML_PAGE" && fail "allow-same-origin was granted"

# The iframe is addressed by a signed, expiring, artifact-bound capability path, because the
# sandbox gives the document an opaque origin that cannot send the session cookie.
EMBED="$(grep -o 'src="/embed/[^"]*"' <<<"$HTML_PAGE" | head -1 | sed 's/^src="//;s/"$//')"
[[ "$EMBED" == /embed/$HTML_ID/*/ ]] || fail "iframe src '$EMBED' is not a capability path"
CAP_TOKEN="${EMBED#/embed/$HTML_ID/}"; CAP_TOKEN="${CAP_TOKEN%/}"
[[ ${#CAP_TOKEN} -ge 20 ]] || fail "capability token is too short to be unguessable"

CSP="$(curl -sS -D - -o "$WORK/raw.html" "$BASE$EMBED" \
       | tr -d '\r' | awk -F': ' '/^[Cc]ontent-[Ss]ecurity-[Pp]olicy:/{print $2}')"
grep -q 'ИНТЕРАКТИВНАЯ-ИНФОГРАФИКА' "$WORK/raw.html" || fail "embed route did not serve the artifact"
grep -q '<script>' "$WORK/raw.html" || fail "inline JS was stripped from an html artifact"
for directive in "default-src 'none'" "connect-src 'none'" "frame-src 'none'" "object-src 'none'" \
                 "form-action 'none'" "base-uri 'none'" "sandbox allow-scripts"; do
  grep -q "$directive" <<<"$CSP" || fail "CSP is missing: $directive"
done
grep -q "img-src $BASE$EMBED data:" <<<"$CSP" || fail "img-src is not pinned to the capability path"
pass "html artifact framed with allow-scripts only; CSP denies network and navigation"

step "the sandboxed document can load its own assets with no cookie"
# Exactly what a browser sends from an opaque origin: no cookie jar, Origin: null. Note the
# absence of -b "$JAR" below -- that is the whole point of the check.
for reference in "assets/chart.png" "chart.png"; do
  HDRS="$(curl -sS -D - -o "$WORK/embed-asset.bin" -w '%{http_code}' \
          -H 'Origin: null' -H 'Sec-Fetch-Site: cross-site' -H 'Sec-Fetch-Mode: no-cors' \
          "$BASE$EMBED$reference" | tr -d '\r')"
  grep -q '200 OK' <<<"$HDRS" || fail "$reference from the sandbox returned: $(head -1 <<<"$HDRS") at $BASE$EMBED$reference"
  grep -qi '^cross-origin-resource-policy: cross-origin' <<<"$HDRS" \
    || fail "$reference carries a CORP an opaque origin cannot satisfy"
  cmp -s "$WORK/embed-asset.bin" "$WORK/chart.png" || fail "$reference served the wrong bytes"
done
pass "assets/chart.png and chart.png load from the opaque origin, cookieless"

step "the capability is narrow"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/embed/$ID/$CAP_TOKEN/")"
[[ "$CODE" == "403" ]] || fail "a capability for one artifact opened another ($CODE)"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/embed/$HTML_ID/${CAP_TOKEN}x/")"
[[ "$CODE" == "403" ]] || fail "a tampered capability token was accepted ($CODE)"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/a/$HTML_ID/source")"
[[ "$CODE" == "403" ]] || fail "the capability leaked access to the source download ($CODE)"
pass "capability is artifact-bound, signature-checked, and opens nothing else"

step "the capability token is kept out of the logs"
docker logs "$NAME" 2>&1 | grep -q "$CAP_TOKEN" && fail "the capability token appears in the logs"
docker logs "$NAME" 2>&1 | grep -q "/embed/$HTML_ID/\*\*\*" || fail "embed requests are not logged at all"
pass "access log records /embed/<id>/*** and never the token"

step "restart the container: artifact, assets and session must survive"
docker restart "$NAME" >/dev/null
for _ in $(seq 1 60); do
  curl -fsS "$BASE/api/health" >/dev/null 2>&1 && break
  sleep 1
done
PAGE2="$(curl -sS -b "$JAR" "$BASE/a/$ID")"
grep -q 'СЕКРЕТНОЕ-ТЕЛО-ДОКУМЕНТА' <<<"$PAGE2" || fail "artifact did not survive the restart"
CT="$(curl -sS -o /dev/null -w '%{content_type}' -b "$JAR" "$BASE/a/$ID/assets/chart.png")"
[[ "$CT" == "image/png" ]] || fail "asset did not survive the restart"
pass "artifact, asset and session survived a real process restart"

step "delete"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE "$BASE/api/artifacts/$ID")"
[[ "$CODE" == "401" ]] || fail "delete without a token returned $CODE, expected 401"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" -X DELETE "$BASE/api/artifacts/$ID")"
[[ "$CODE" == "401" ]] || fail "a viewer session authorised deletion ($CODE)"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE "$BASE/api/artifacts/$ID" \
        -H "Authorization: Bearer $TOKEN")"
[[ "$CODE" == "204" ]] || fail "delete returned $CODE, expected 204"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/a/$ID")"
[[ "$CODE" == "404" ]] || fail "deleted artifact still returns $CODE"
docker exec "$NAME" sh -c "test ! -e /data/artifacts/$ID" || fail "artifact bytes survived deletion"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE "$BASE/api/artifacts/$ID" \
        -H "Authorization: Bearer $TOKEN")"
[[ "$CODE" == "404" ]] || fail "second delete returned $CODE, expected 404"
pass "204 then 404; every byte removed from /data"

step "a spoofed X-Forwarded-For cannot buy a fresh rate-limit bucket"
# FORWARDED_ALLOW_IPS is deliberately unset above, so uvicorn trusts 127.0.0.1 only and the
# container's real peer (the docker bridge) stays the throttle key for every attempt below.
# With the old `--forwarded-allow-ips *` in the image this loop never reached 429.
THROTTLED=0
for i in $(seq 1 8); do
  CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/login" \
          -H "X-Forwarded-For: 203.0.113.$i" \
          --data-urlencode "password=wrong-password-$i" --data-urlencode "next=/")"
  [[ "$CODE" == "429" ]] && { THROTTLED=1; break; }
done
[[ "$THROTTLED" == "1" ]] || fail "8 failed logins from 8 forged addresses were never throttled"
pass "forged X-Forwarded-For is ignored; the throttle still fires"

step "logs must not contain secrets"
LOGS="$(docker logs "$NAME" 2>&1)"
grep -q "$TOKEN" <<<"$LOGS" && fail "the bearer token appears in the logs"
grep -q "$PASSWORD" <<<"$LOGS" && fail "the viewer password appears in the logs"
grep -q "$SESSION_KEY" <<<"$LOGS" && fail "the session key appears in the logs"
grep -q 'СЕКРЕТНОЕ-ТЕЛО-ДОКУМЕНТА' <<<"$LOGS" && fail "an artifact body appears in the logs"
grep -qi 'ap_session=' <<<"$LOGS" && fail "a session cookie appears in the logs"
grep -q '"event": "request"' <<<"$LOGS" || fail "structured access log lines are missing"
pass "structured logs present, no secrets, no bodies"

printf '\nSMOKE TEST PASSED\n'
