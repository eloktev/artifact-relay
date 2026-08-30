# Vendored browser assets

Bundled deliberately instead of loaded from a CDN: artifact pages must not make third-party
network requests, and the viewer's Content-Security-Policy forbids remote scripts.

| File | Upstream | Version | SHA-256 |
| --- | --- | --- | --- |
| `mermaid.min.js` | https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js | 11.12.0 | `07e37dfa97b337ccc85365d57eddf99b9706f09db3b59b260d0333b23b343c4b` |

Verify with:

```sh
shasum -a 256 src/artifact_relay/static/js/mermaid.min.js
```

The bundle was checked to contain no `eval(`, no `new Function` and no dynamic `import()`,
which is why the viewer CSP needs neither `'unsafe-eval'` nor a remote origin.
