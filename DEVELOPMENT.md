# Development log — Artifact Relay V1

Strict vertical TDD. For every behaviour: write one failing test, run it, record the
**RED** command and its real output, then write the minimum production code and record the
**GREEN** command and its real output.

All outputs below are copied from actual terminal runs (trimmed to the relevant lines only —
never rewritten). Environment: macOS 24.6.0 (arm64), CPython 3.12.9, `uv` 0.7.0.

## Cycle 0 — project scaffold (no behaviour yet)

```
$ uv venv --python 3.12
Using CPython 3.12.9 interpreter at: /opt/homebrew/opt/python@3.12/bin/python3.12
Creating virtual environment at: .venv

$ uv sync --extra dev
... resolved + installed 60 packages, uv.lock written
```

## Cycle 1 — `GET /api/health` leaks no secrets

RED — `tests/test_health.py` written first, package does not exist yet:

```
$ .venv/bin/python -m pytest tests/test_health.py
>       from artifact_relay.config import Settings
E       ModuleNotFoundError: No module named 'artifact_relay'
1 error in 0.03s
```

GREEN — added `config.Settings` (all secrets wrapped in `SecretStr`) and `app.create_app`:

```
$ .venv/bin/python -m pytest tests/test_health.py
1 passed, 1 warning in 0.40s
```

## Cycle 2 — bearer auth, including the constant-time boundary

RED — `tests/test_api_auth.py` (16 cases: missing/malformed headers, near-miss tokens that
share a prefix or differ only in the last byte, and a spy asserting `hmac.compare_digest`
is what performs the comparison):

```
$ .venv/bin/python -m pytest tests/test_api_auth.py
FAILED tests/test_api_auth.py::test_bearer_comparison_is_constant_time - Impo...
FAILED tests/test_api_auth.py::test_malformed_authorization_headers_are_rejected[Basic abc]
... 16 failed, 1 warning in 0.19s
```

The first version of `test_publish_with_valid_token_passes_authentication` asserted only
`status_code != 401`, which passed vacuously against a missing route (404). It was tightened
to `not in (401, 404, 405)` and re-run to obtain a real RED:

```
$ .venv/bin/python -m pytest tests/test_api_auth.py::test_publish_with_valid_token_passes_authentication
>       assert response.status_code not in (401, 404, 405), response.text
E       assert 404 not in (401, 404, 405)
1 failed, 1 warning in 0.16s
```

GREEN — `security.verify_bearer_token` + `dependencies.require_api_token` (the dependency
runs before body parsing, so an anonymous caller never makes the server buffer an upload):

```
$ .venv/bin/python -m pytest tests/test_api_auth.py tests/test_health.py
18 passed, 1 warning in 0.17s
```

## Cycle 3 — publish returns id/url/created_at/expires_at

RED:

```
$ .venv/bin/python -m pytest tests/test_publish.py
>       assert response.status_code == 201, response.text
E       AssertionError: {"detail":"not implemented"}
E       assert 501 == 201
1 failed, 1 warning in 0.17s
```

GREEN — `models.py`, `db.py` (SQLite/WAL schema), `storage.ArtifactStore` (stage in `tmp/`,
`fsync`, single `os.replace`, only then insert metadata) and the real `POST /api/artifacts`:

```
$ .venv/bin/python -m pytest tests/test_publish.py
1 passed, 1 warning in 0.19s
```

## Cycle 4 — opaque artifact identifiers

`tests/test_artifact_ids.py` **passed on first run**: `storage.new_artifact_id` had already
been written in cycle 3 as `secrets.token_urlsafe(24)`. No RED was observed, so the test was
mutation-checked instead — `new_artifact_id` was temporarily replaced with a counter:

```
$ .venv/bin/python -m pytest tests/test_artifact_ids.py      # with the counter mutant
>           assert len(artifact_id) * 6 >= 128, artifact_id
E           AssertionError: art000001
E           assert (9 * 6) >= 128
E            +  where 9 = len('art000001')

$ .venv/bin/python -m pytest tests/test_artifact_ids.py      # original restored
2 passed, 1 warning in 0.21s
```

## Cycle 5 — login page and hardened session cookie

RED:

```
$ .venv/bin/python -m pytest tests/test_auth_session.py
>       assert response.status_code == 200
E       assert 404 == 200
>       assert response.status_code == 303
E       assert 404 == 303
2 failed, 1 warning in 0.17s
```

GREEN — `security.SessionSigner` (itsdangerous, server-side max-age), `verify_view_password`
(Argon2id), `routers/auth.py`, `templates/base.html`, `templates/login.html`:

```
$ .venv/bin/python -m pytest tests/test_auth_session.py
2 passed, 1 warning in 0.49s
```

## Cycle 6 — wrong password, open-redirect defence, logout

The 11 new cases passed immediately, because `safe_next_path` and `/logout` had been written
in cycle 5 — an over-implementation on my part, recorded here rather than hidden. Teeth were
proven by mutating `safe_next_path` down to `return value or fallback`:

```
$ .venv/bin/python -m pytest tests/test_auth_session.py     # with the mutant
E       AssertionError: https://evil.example/steal
E       assert 'https://evil.example/steal' == '/'
E       AssertionError: //evil.example/steal
E       assert '//evil.example/steal' == '/'

$ .venv/bin/python -m pytest tests/test_auth_session.py     # original restored
13 passed, 1 warning in 0.28s
```

## Cycle 7 — bounded login throttling

RED:

```
$ .venv/bin/python -m pytest tests/test_login_ratelimit.py
>       assert blocked.status_code == 429
E       assert 401 == 429
E       ModuleNotFoundError: No module named 'artifact_relay.ratelimit'
3 failed, 1 passed, 1 warning in 0.20s
```

GREEN — `ratelimit.FixedWindowRateLimiter` with LRU-capped key table, wired into `POST /login`
(throttle checked *before* the password is verified; a success resets the window):

```
$ .venv/bin/python -m pytest tests/test_login_ratelimit.py
4 passed, 1 warning in 0.18s
```

## Cycle 8 — anonymous visitors get an OG shell, never the body

RED:

```
$ .venv/bin/python -m pytest tests/test_viewer_access.py
>       assert page.status_code == 200
E       assert 404 == 200
2 failed, 1 passed, 1 warning in 0.23s
```

GREEN — `routers/viewer.py` answers **200 with `shell.html`** (not a redirect) for anonymous
requests, because Telegram's crawler needs the Open Graph tags at the canonical artifact URL
and does not follow login redirects. Second RED/GREEN inside the cycle for the header:

```
E       KeyError: 'x-robots-tag'
```

`middleware.SecurityHeadersMiddleware` added (`X-Robots-Tag`, `nosniff`, `Referrer-Policy`,
`COOP`, `Permissions-Policy`, viewer CSP):

```
$ .venv/bin/python -m pytest tests/test_viewer_access.py
3 passed, 1 warning in 0.20s
```

## Cycle 9 — Markdown sanitisation

RED:

```
$ .venv/bin/python -m pytest tests/test_markdown_sanitization.py
E       ModuleNotFoundError: No module named 'artifact_relay.rendering'
5 failed
```

First implementation attempt surfaced two honest problems, both recorded rather than papered
over:

```
E       ValueError: "rel" attribute is not allowed for tag "a" when link_rel is set
E       AssertionError: 'javascript:' survived sanitisation
E           >[ссылка](javascript:alert('xss'))</p>
```

The second one was a **bad test, not a bad implementation**: markdown-it rejects the
`javascript:` destination and leaves the link as inert *text*, so the substring survives
harmlessly. The test was rewritten to parse the DOM and assert on tags and attribute values
(no `on*` handlers, no `style`, no `javascript:`/`vbscript:`, `data:` only as `img[src]` with
an `image/` type, no `evil.example` anywhere) — a stronger check than substring matching.

GREEN — `rendering.py`: markdown-it renders, then the whole output passes through `nh3`
(Rust `ammonia`) with an explicit tag/attribute allowlist, `url_schemes`, an
`attribute_filter` and `link_rel="noopener noreferrer nofollow"`:

```
$ .venv/bin/python -m pytest tests/test_markdown_sanitization.py
4 passed, 1 warning in 0.20s
```

## Cycle 10 — document structure, TOC, locally bundled Mermaid

RED (the end-to-end sanitisation test of cycle 9 was passing vacuously against the `<pre>`
placeholder; this cycle's tests exposed that):

```
$ .venv/bin/python -m pytest tests/test_markdown_page.py
E       assert '<table' in '<!doctype html>...</pre>\n</article>...'
E           AssertionError: missing anchor target h-отчёт-о-миграции
E       assert '<div class="mermaid">' in '...'
3 failed, 2 passed, 1 warning in 0.25s
```

GREEN — Pygments fence rendering, stable unique heading anchors + TOC extraction, a
`mermaid` fence renderer, `templates/artifact.html`, and Mermaid **11.12.0 vendored into
`static/js/`** (checked to contain no `eval(`, no `new Function` and no dynamic `import()`,
so the CSP needs no `'unsafe-eval'`; SHA-256 recorded in `static/js/VENDOR.md`):

```
$ .venv/bin/python -m pytest tests/test_markdown_page.py
5 passed, 1 warning in 0.23s

$ .venv/bin/python -m pytest
50 passed, 1 warning in 0.53s
```

## Cycle 11 — standalone HTML runs in a sandboxed, network-denied iframe

RED:

```
$ .venv/bin/python -m pytest tests/test_html_artifact_sandbox.py
E       AssertionError: artifact HTML was inlined into the viewer page
E       assert 'ИНФОГРАФИКА-МАРКЕР' not in '<!doctype h...dy>\n</html>'
```

GREEN — `csp.artifact_csp()` plus `GET /a/{id}/raw`. The body is never inlined into the
viewer page: it is loaded into `<iframe sandbox="allow-scripts">` from its own URL, so it
gets an opaque origin and cannot read `document.cookie`. The response repeats
`sandbox allow-scripts` as a CSP directive and denies `connect-src`, `frame-src`,
`child-src`, `worker-src`, `manifest-src`, `object-src`, `form-action` and `base-uri`;
`img-src`/`font-src`/`media-src` name only the artifact's own absolute asset prefix
(`'self'` is meaningless in an opaque origin).

```
$ .venv/bin/python -m pytest tests/test_html_artifact_sandbox.py
5 passed, 1 warning in 0.23s
```

## Cycle 12 — attachments, media types and path traversal

RED:

```
$ .venv/bin/python -m pytest tests/test_assets.py
E       AssertionError: assert [] == ['chart.png', 'data.csv']
E       AssertionError: assert 404 == 403
```

Partial GREEN left one honest failure, which turned out to be a **test-harness artefact**:

```
E       AssertionError: 200
E       assert 200 in (400, 404)
FAILED tests/test_assets.py::test_asset_path_traversal_is_impossible[..]
1 failed, 24 passed
```

`httpx` normalises a literal `..` in the URL *client-side* (verified: `/a/ID/assets/..`
leaves the client as `/a/ID`), so that case never reaches the server as traversal. The
parameter list was switched to the percent-encoded forms that do arrive verbatim
(`%2e%2e`, `%2e`, `%2e%2e%5c…`), and a client-independent unit test of
`assets.is_safe_asset_name` was added for the whole hostile set.

GREEN — `assets.py` (flat ASCII-only name allowlist, reject rather than rewrite), asset
persistence in the publish endpoint, and `GET /a/{id}/assets/{name}` with three independent
gates: name allowlist, metadata lookup, and a resolved-path containment check. Active
content (`.html`, `.js`, unknown types) is forced to `application/octet-stream` +
`Content-Disposition: attachment`; every asset response carries
`default-src 'none'; sandbox`, so even a direct navigation to an SVG is inert.

```
$ .venv/bin/python -m pytest tests/test_assets.py
44 passed, 10 warnings in 0.42s
```

## Cycle 13 — payload limits

RED:

```
$ .venv/bin/python -m pytest tests/test_limits.py
E       AssertionError: assert 201 == 422    (blank title)
E       assert 201 == 422                    (over-long summary)
E       assert 201 == 413                    (over-sized content)
```

GREEN — `validation.py` (413 for "too big", 422 for "malformed"), all checks running before
any bytes are written, plus a `Content-Length` pre-check dependency so an over-sized upload
is refused before it is buffered:

```
$ .venv/bin/python -m pytest tests/test_limits.py
22 passed, 2 warnings in 0.33s
```

## Cycle 14 — expiry returns 410 everywhere

RED:

```
$ .venv/bin/python -m pytest tests/test_expiry.py
E       AssertionError: 404
E       assert 404 == 410
E       AssertionError: assert 'text/html' in 'application/json'
3 failed, 4 passed
```

A self-inflicted incident worth recording: the first GREEN attempt was issued as one shell
command containing the stray line `cat > src/antml_tmp`, which reads **stdin** and blocked
until the 2-minute tool timeout. Everything after it silently never ran, and the next test
run looked like an implementation failure. Verified with `grep -n "^@router"` that the
routes had not in fact been written, then re-applied the change.

`errors.wants_html` was also corrected during this cycle: it originally keyed off the
`Accept` header, but Telegram's in-app browser and several crawlers send `Accept: */*` and
would have been handed raw JSON. It now keys off the path prefix (`/api/` -> JSON,
everything else -> rendered page).

GREEN — `errors.py` (HTML error pages), `download.py` + `GET /a/{id}/source`
(always an attachment; an HTML artifact's own source is served as `text/plain` so it can
never execute on this origin).

## Cycle 15 — Open Graph card

RED:

```
$ .venv/bin/python -m pytest tests/test_og_image.py
E       AssertionError: the OG card must not require a session
E       assert 404 == 200
E       PIL.UnidentifiedImageError: cannot identify image file <_io.BytesIO object ...>
E       AssertionError: different titles must produce different cards
```

GREEN — `ogimage.render_card` (1200x630, DejaVu Sans vendored for Cyrillic, deterministic
output, no EXIF/pnginfo/timestamps) and the unauthenticated `GET /a/{id}/og.png`:

```
$ .venv/bin/python -m pytest tests/test_og_image.py tests/test_expiry.py
13 passed, 1 warning in 1.24s

$ .venv/bin/python -m pytest
134 passed, 1 warning in 1.31s
```

## Cycle 16 — delete API (and a real bug the test caught)

RED:

```
$ .venv/bin/python -m pytest tests/test_delete.py
E       assert 404 == 204
E       AssertionError: assert 404 == 401
```

`test_delete_rejects_hostile_identifiers` exposed a genuine defect in the cycle-3 code:
`ArtifactStore.delete` called `shutil.rmtree(self.artifact_dir(artifact_id))`
**unconditionally**, so an id of `..` resolved to the data directory itself. Proven by
removing the fix and re-running the regression test — the mutant destroys the database:

```
$ .venv/bin/python -m pytest tests/test_delete.py::test_store_never_walks_outside_its_own_directory
>           assert store.delete(hostile) is False
E           sqlite3.OperationalError: no such table: artifacts
1 failed in 0.03s
```

GREEN — `storage.is_valid_artifact_id` now gates every filesystem and SQL path
(`artifact_dir` raises, `get`/`delete`/`get_asset`/`list_assets` return empty), plus
`DELETE /api/artifacts/{id}`:

```
$ .venv/bin/python -m pytest tests/test_delete.py
7 passed, 1 warning in 0.28s
```

## Cycle 17 — janitor

RED:

```
$ .venv/bin/python -m pytest tests/test_janitor.py
E       ModuleNotFoundError: No module named 'artifact_relay.janitor'
```

GREEN — `janitor.py` with three idempotent jobs (expired artifacts, orphan directories,
stale staging directories). The orphan rule needed a grace period: a directory that has just
appeared may be a publish in flight between `os.replace` and the metadata `INSERT`, and
deleting it would destroy live bytes. The test was updated to back-date the orphan's mtime
and to assert that a *fresh* orphan is deliberately left alone.

```
$ .venv/bin/python -m pytest tests/test_janitor.py
6 passed, 1 warning in 0.27s
```

## Cycle 18 — restart persistence

`tests/test_persistence.py` passed on first run (storage was already durable). Two mutation
checks were run to see what the tests really pin down. The first was a **bad mutation** and
is recorded as such: randomising `SESSION_SALT` per import does not simulate a restart,
because both `TestClient`s live in the same process, so it passed 4/4. The second mutation —
`initialize()` wiping `artifacts/` — failed correctly:

```
$ .venv/bin/python -m pytest tests/test_persistence.py    # with the wiping mutant
E       FileNotFoundError: [Errno 2] No such file or directory: '.../artifacts/meBYTp.../source'
1 failed, 3 passed, 1 warning in 0.47s

$ .venv/bin/python -m pytest tests/test_persistence.py    # original restored
4 passed, 1 warning in 0.27s
```

Honest limitation: these tests restart the *application object*, not the OS process. A true
process restart is covered by the Docker smoke test at the end of this document.

## Cycle 19 — structured logs that cannot leak

RED:

```
$ .venv/bin/python -m pytest tests/test_logging.py
E       ModuleNotFoundError: No module named 'artifact_relay.logging_setup'
4 failed, 1 warning in 0.24s
```

GREEN — `logging_setup.JsonFormatter` (secret values replaced with `***` in the fully
rendered line, tracebacks included; field names like `authorization`/`cookie`/`password`
dropped wholesale) and `middleware.AccessLogMiddleware` (method, path, status, duration,
request id — no query string, no headers, no body):

```
$ .venv/bin/python -m pytest tests/test_logging.py
4 passed, 1 warning in 0.26s
```

## Cycle 20 — OpenAPI schema and the root route

RED (only the root route was missing; the schema assertions already held):

```
$ .venv/bin/python -m pytest tests/test_openapi.py
E       assert 404 == 303
1 failed, 4 passed, 1 warning in 0.26s
```

GREEN — `GET /` redirects to `/login`. There is deliberately no artifact index: links are
the only way in.

## Cycle 20a — a real flake found by running the whole suite

`tests/test_janitor.py::test_sweep_removes_stale_staging_directories` passed alone but
failed in the full suite:

```
$ .venv/bin/python -m pytest
FAILED tests/test_janitor.py::test_sweep_removes_stale_staging_directories
1 failed, 154 passed, 1 warning in 1.76s

>       assert result.staging == 1
E       assert 0 == 1
E        +  where 0 = SweepResult(expired=0, orphans=0, staging=0).staging
```

Reproduced outside pytest, which showed the directory *was* deleted — just not by the call
under test (`entries after: ['stage-cafebabe']`, `result: SweepResult(..., staging=0)`).
The app's own background janitor had swept first. Fixed in **production code**, not the
test: `run_janitor` now waits one interval *before* its first sweep, because
`startup_sweep()` has already run by the time the task starts, so sweeping immediately only
duplicated it. Three consecutive full runs:

```
$ .venv/bin/python -m pytest      (x3)
155 passed, 1 warning in 1.69s
155 passed, 1 warning in 1.70s
155 passed, 1 warning in 1.68s
```

## Cycle 21 — mobile table wrapping and task lists

RED:

```
E       assert '<div class="doc__table">' in '<table>\n<thead>...'
E       assert 'class="task-list-item"' in '<ul class="contains-task-list">\n<li class="task-list-item enabled">...'
```

The second failure was again the test being wrong: the plugin emits
`class="task-list-item enabled"`, so the exact-attribute assertion was replaced with a
substring check on the class list. GREEN — `table_open`/`table_close` render rules wrap
tables in `<div class="doc__table">` so a wide table scrolls in its own box on a phone.

## Cycle 22 — ASGI entrypoint and the password-hashing helper

RED:

```
E       ImportError: cannot import name 'main' from 'artifact_relay'
E       ModuleNotFoundError: No module named 'artifact_relay.hashing'
```

GREEN — `main.py` (settings read at import time so a missing secret kills the container
immediately rather than surfacing as a 500 later) and `hashing.py`
(`python -m artifact_relay.hashing`, plaintext read via `getpass`, only the hash
printed):

```
$ .venv/bin/python -m pytest tests/test_entrypoint.py
4 passed in 0.56s
```

## Cycle 23 — vendored-asset and repository-hygiene tests

`tests/test_vendored_assets.py` pins the things that would otherwise rot silently: the
Mermaid bundle is present and not truncated, its SHA-256 matches `static/js/VENDOR.md`, it
contains no `eval(`/`new Function`/dynamic `import()`, the static mount actually serves it
alongside the fonts and stylesheets, no CSS or JS references a remote origin, the DejaVu
licence ships with the fonts, and `highlight.css` still matches its generator.

The hygiene test went through two honest corrections:

1. The first version flagged the deliberately-broken `$argon2id$broken` literals added in
   cycle 24's negative tests — a correct catch of my own fixtures.
2. The obvious fix (allow anything `argon2-cffi` rejects) does not work: `argon2-cffi`
   reports a malformed hash and a wrong password with the *same* `VerificationError`.
   Verified directly:

```
$ .venv/bin/python -c "... h.verify(lit, 'pw') ..."
'$argon2id$broken' -> argon2.exceptions.VerificationError
'$argon2id$' -> argon2.exceptions.VerificationError
'$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000' -> argon2.exceptions.VerificationError
```

The check is therefore **structural**: a genuine encoded hash has six `$`-separated fields;
truncated fixtures cannot authenticate anyone, and the single full-length placeholder has an
all-zero digest.

## Cycle 24 — closing the coverage gaps that mattered

The first full coverage run reported 95% and, more usefully, showed that
`dependencies.enforce_request_size` — a DoS control — had **no test at all**. Tests were
added for it and for the other branches that carry real behaviour: weak-secret rejection at
start-up, OG-card title truncation and unbreakable words, unknown/absent fence languages,
Mermaid diagram source escaping, the ASCII fallback in `Content-Disposition` for a fully
Cyrillic title, blank summaries, duplicate asset names, the access log's failure path,
the hashing CLI, `data:image` surviving only as `img[src]`, `target="_blank"` handling,
heading-only `id`s, malformed password hashes, control characters in `next`, and expired /
foreign-key session signatures.

```
$ .venv/bin/python -m pytest -q --cov=artifact_relay --cov-report=term
TOTAL                                         1062     25    98%
```

The remaining 25 uncovered lines are defensive `except`/cleanup paths (`shutil.rmtree` after
a failed publish, `models.is_pinned`, `build_assets.main`) that cannot be triggered without
faulting the filesystem.

---

# Final quality gates

Every command below was run against the final tree on macOS 24.6.0 (arm64), CPython 3.12.9,
`uv` 0.7.0, Docker 28.x. Output is copied verbatim (trimmed to the result lines).

## Static analysis

```
$ .venv/bin/ruff check .
All checks passed!
[exit=0]

$ .venv/bin/ruff format --check .
54 files already formatted
[exit=0]

$ .venv/bin/mypy
Success: no issues found in 33 source files
[exit=0]
```

`mypy` runs in `strict` mode (`disallow_untyped_defs`, `warn_unused_ignores`) over the
`artifact_relay` package. One narrow `# type: ignore[no-untyped-call]` exists, on
`HtmlFormatter.get_style_defs`, which `types-Pygments` leaves unannotated.

## Lock file

```
$ uv lock --check
Resolved 73 packages in 1ms
[exit=0]
```

## Tests

```
$ .venv/bin/python -m pytest      (three consecutive runs)
205 passed, 1 warning in 3.67s
205 passed, 1 warning in 3.71s
205 passed, 1 warning in 3.64s
```

The single warning is `StarletteDeprecationWarning: Using httpx with starlette.testclient is
deprecated; install httpx2 instead` — emitted by `fastapi.testclient` on import, not by this
project's code.

```
$ .venv/bin/python -m pytest -q --cov=artifact_relay --cov-report=term
TOTAL                                         1062     25    98%
```

## Dependency audit

`pip-audit` is run against the **locked** dependency set rather than the installed
environment, because the local project itself is not on PyPI (`--strict` would otherwise
fail with "Dependency not found on PyPI"), and because the lock file is exactly what ships
in the image.

```
$ uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/req-prod.txt
$ .venv/bin/pip-audit --strict --progress-spinner=off -r /tmp/req-prod.txt
No known vulnerabilities found
[exit=0]

$ uv export --frozen --all-extras --no-emit-project --format requirements-txt -o /tmp/req-all.txt
$ .venv/bin/pip-audit --strict --progress-spinner=off -r /tmp/req-all.txt
No known vulnerabilities found
[exit=0]
```

The dev audit initially failed and the finding was fixed rather than suppressed:

```
Found 1 known vulnerability in 1 package
Name   Version ID              Fix Versions
------ ------- --------------- ------------
pytest 8.4.2   PYSEC-2026-1845 9.0.3
```

`pyproject.toml` now requires `pytest>=9.0.3`; the installed version is 9.1.1 and the suite
passes unchanged on it.

## Generated assets

```
$ .venv/bin/python -m artifact_relay.build_assets
wrote .../static/css/highlight.css
$ diff -q /tmp/hl-before.css src/artifact_relay/static/css/highlight.css
IDENTICAL — generator matches committed asset
```

## Docker image

```
$ docker build -t artifact-relay:local .
[exit=0]  →  artifact-relay:local  230MB
```

An early build produced an image that could not import its own package:

```
$ docker run --rm artifact-relay:local python -c "import artifact_relay"
ModuleNotFoundError: No module named 'artifact_relay'
```

Cause: `uv sync` installs the workspace project **editable** by default, leaving a `.pth`
pointing at `/build/src`, which does not exist in the runtime stage. Fixed with
`--no-editable` in both `uv sync` invocations. After the fix:

```
$ docker run --rm artifact-relay:local python -c "import sys, artifact_relay; print(...)"
3.12.14 1.1.0

$ docker run --rm artifact-relay:local id
uid=10001(app) gid=10001(app) groups=10001(app)

$ docker run --rm artifact-relay:local sh -c 'ls -ldn /data; touch /data/probe && echo WRITABLE'
drwxr-xr-x 2 10001 10001 4096 ... /data
WRITABLE

$ docker inspect -f 'User={{.Config.User}} Volumes={{json .Config.Volumes}}' artifact-relay:local
User=app:app Volumes={"/data":{}}
```

Package data (templates, vendored Mermaid, DejaVu fonts, stylesheets) is present in
`site-packages`, verified by listing them inside the container.

## Container smoke test

`scripts/smoke.sh` is the reproducible version of the manual walkthrough; CI runs the same
script. It generates throwaway credentials per run, starts a container against a fresh named
volume, and asserts 19 properties end to end.

```
$ ./scripts/smoke.sh artifact-relay:local 18082
  ok   argon2id hash generated
  ok   GET /api/health -> {"status":"ok"}
  ok   docker healthcheck -> healthy
  ok   uid 10001, /data writable
  ok   publish rejects missing and wrong bearer tokens
  ok   published 1dVCuUTph4aG5LGOQxqSHapnOdIXUMSj
  ok   shell has OG tags + login form, body withheld
  ok   og.png is 18744 bytes of PNG
  ok   raw/source/assets are 403 or 404 without a session
  ok   wrong password -> 401; correct password -> 303 back to the artifact
  ok   external next falls back to /
  ok   markdown rendered, sanitised, TOC present, mermaid bundled locally
  ok   mermaid.min.js served locally, 2748992 bytes, no eval
  ok   source is byte-identical and downloads; asset served as image/png
  ok   encoded traversal attempts return 404/400 and leak nothing
  ok   html artifact framed with allow-scripts only; CSP denies network and navigation
  ok   artifact, asset and session survived a real process restart
  ok   204 then 404; every byte removed from /data
  ok   structured logs present, no secrets, no bodies

SMOKE TEST PASSED
[exit=0]
```

The restart step is a genuine `docker restart` — a real OS process restart, which is the
thing `tests/test_persistence.py` could only approximate by rebuilding the application
object in-process.

## CI

`.github/workflows/ci.yml` parses as valid YAML and defines three jobs:

```
quality -> checkout, install uv, uv sync --frozen, uv lock --check, ruff check,
           ruff format --check, mypy, pytest --cov, generated-assets diff,
           vendored-bundle SHA check
audit   -> checkout, install uv, uv sync, export locked deps, pip-audit (runtime),
           pip-audit (incl. dev)
docker  -> checkout, buildx, build image, ./scripts/smoke.sh
```

## Independent security review and remediation

A fresh Claude `-p` review returned **no-ship** on two deployment blockers and two broken
asset flows:

- uvicorn trusted every `X-Forwarded-For`, so the per-client login throttle was spoofable;
- Argon2 verification had no process-wide concurrency ceiling;
- relative assets in Markdown resolved outside the artifact;
- sandboxed HTML could not authenticate its own opaque-origin asset requests;
- authenticated multipart uploads without `Content-Length` could be read without a transport
  ceiling.

A separate Claude `-p` fix session added focused regression suites
(`test_proxy_trust.py`, `test_login_verification_gate.py`, `test_markdown_assets.py`,
`test_embed_capability.py`, and `test_streaming_limits.py`) and implemented the narrow fixes:

- `FORWARDED_ALLOW_IPS` now actually controls uvicorn, defaulting to loopback;
- a bounded process-wide gate caps concurrent Argon2 work;
- Markdown relative references are rewritten to the artifact's own asset route;
- standalone HTML uses a signed, expiring, artifact-bound embed capability. It grants only
  the iframe document and that artifact's assets, works without cookies, and is redacted from
  logs;
- an ASGI receive-channel ceiling stops chunked multipart bodies before form parsing can
  drain them indefinitely;
- the accidental `src/antml_tmp` file was removed, test-package behavior was normalized,
  and filename/error-route edge cases were fixed.

The first expanded smoke run caught three faults in the smoke script itself: it rejected the
external image that the regression intentionally preserves, forgot to upload the HTML fixture's
asset, and reused `TOKEN` for both the publisher bearer credential and the embed capability.
Those assertions/setup variables were corrected; production code was not weakened.

Final local evidence after remediation:

```text
ruff check .                    All checks passed
ruff format --check .           65 files already formatted
mypy                            no issues in 34 source files
pytest                          307 tests collected; full suite passed
pip-audit runtime + dev         No known vulnerabilities found (both)
uv lock --check                 resolved 73 packages; exit 0
docker build                    artifact-relay:local built successfully
scripts/smoke.sh :18088         SMOKE TEST PASSED
```

The smoke test now proves publish → anonymous OG shell → login → rendered Markdown + both
relative asset spellings → local Mermaid → sandboxed HTML + cookieless capability assets →
capability isolation/redaction → real container restart persistence → delete → spoofed-XFF
throttling → secret-free structured logs.

## Known blockers and limitations

- **Not deployed.** No remote repository was created, nothing was pushed, and no live
  infrastructure was touched. The generic VPS section of the README is written but unexercised.
- **CI is unexecuted.** The workflow is syntactically valid and every step it runs was run
  locally, but GitHub Actions itself has not run it.
- **Single replica only.** SQLite plus an in-process rate limiter and janitor.
- **One test warning** comes from `fastapi.testclient` (httpx vs httpx2), not from this code.
