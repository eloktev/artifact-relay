# Publish from any shell-capable agent

`scripts/publish-file.sh` gives Claude Code, Codex, OpenCode, and other shell-capable
agents a narrow publish command. It accepts an existing Markdown or standalone HTML file and
prints the private artifact URL on success.

For a newly bootstrapped checkout, start with the
[bounded first-artifact example](../scripts/publish-example.sh). It publishes the bundled report
using the repository's bootstrap-generated `.env`; an optional HTTPS origin (or loopback HTTP
origin) may be its only argument. Use the arbitrary-file workflow below once that activation
check succeeds.

## Keep the publisher token out of the conversation

Before starting the agent, provide `ARTIFACT_RELAY_API_TOKEN` through the process environment
using your OS, runner, or agent launcher's secret mechanism. Do not paste the value into chat,
an agent configuration file, source code, command arguments, or an artifact. The agent only
needs to know the environment variable's name.

You can check that the launcher supplied a non-empty value without displaying it:

```sh
test -n "${ARTIFACT_RELAY_API_TOKEN:-}" || printf 'publisher token is not available\n' >&2
```

The helper reads the token from the environment and feeds the Authorization header to `curl`
through standard input, so the token is not placed in `curl`'s process arguments. It suppresses
response bodies and transport diagnostics on failure to avoid relaying secret-bearing server
output.

## Publish a file

Requirements are a POSIX shell, `curl`, `mktemp`, and Python 3.12 (the repository's supported
Python). The helper checks that each command is available before making a request.
The default relay is `http://localhost:8000`:

```sh
./scripts/publish-file.sh report.md
```

For another deployment, pass only its origin through the environment. HTTPS is required unless
the host is `localhost` or a loopback IP address:

```sh
ARTIFACT_RELAY_BASE_URL=https://artifacts.example.com \
  ./scripts/publish-file.sh --title 'Release report' --summary 'Deployment result' report.md
```

The extension selects `markdown` for `.md`/`.markdown` and `html` for `.html`/`.htm`,
case-insensitively. For unambiguous `curl` multipart handling, the input path must not contain a
comma, semicolon, quote, or backslash. Override detection for another file extension:

```sh
./scripts/publish-file.sh --format markdown result.txt
```

Use `--expires-in-days DAYS` to select retention; the helper sends `30` by default, and `0`
requests no expiry. Run `./scripts/publish-file.sh --help` for all options.

The helper calls `POST /api/artifacts` as multipart form data with `title`, `format`,
`expires_in_days`, optional `summary`, and `content=@file`. It does not upload assets or set
agent-specific provenance fields. Published artifacts remain subject to the relay's configured
validation and maximum TTL. A successful command prints only the `url` returned by the API. That
response URL may use a different public origin from the request origin, but it must use HTTPS
unless it addresses `localhost` or a loopback IP.

## Publish from GitHub Actions

The repository root contains a composite action that wraps the same hardened helper. It accepts a
runner file path and non-secret metadata as action inputs. The publisher token remains a step-level
GitHub Actions secret, not an action input or command argument:

```yaml
name: Publish report
on:
  workflow_dispatch:

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - name: Build report
        run: mkdir -p build && printf '# Deployment report\n\nAll checks passed.\n' > build/report.md
      - name: Publish private report
        id: relay
        uses: eloktev/artifact-relay@v1.3.0
        env:
          ARTIFACT_RELAY_API_TOKEN: ${{ secrets.ARTIFACT_RELAY_API_TOKEN }}
        with:
          relay-url: https://artifacts.example.com
          artifact-path: build/report.md
          title: Deployment report
          expires-in-days: 30
```

The runner needs Bash, a POSIX shell, `curl`, `mktemp`, and Python 3. The action outputs only the
validated `artifact-url`; it never emits the token. A private artifact URL can still be sensitive:
avoid printing it from public-repository workflows, job summaries, annotations, or pull-request
comments unless the intended audience is allowed to reach the viewer login.

Pin `uses:` to a release tag or, for an immutable reference, a full commit SHA. Fork-based pull requests do not receive repository
secrets by default; do not weaken that GitHub protection or switch to `pull_request_target` merely to
publish untrusted changes. Use the action only after your report-producing step has completed and the
runner identity is eligible to read the publisher secret.
