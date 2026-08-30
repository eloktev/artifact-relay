# Security Policy

## Supported versions

Security fixes are released for the latest published release. Deploy an immutable
image tag or digest and follow the upgrade procedure in `docs/UPGRADE_ROLLBACK.md`.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use the
repository host's **private security advisory** feature to report it to the
maintainers. If private advisories are unavailable, contact a maintainer through
a private channel listed on their repository profile and ask for a secure
reporting address before sending sensitive details.

Include the affected version, deployment shape, reproduction steps, impact, and
any suggested mitigation. Do not include real credentials, private artifact
content, or active share links. Maintainers will acknowledge a complete report
as soon as practical and coordinate disclosure after a fix is available.

## Deployment security

The localhost Compose file binds only to loopback and disables secure cookies
only because it uses plain HTTP. Internet-facing deployments must terminate TLS,
set `COOKIE_SECURE=true`, restrict trusted proxy addresses, protect `.env` with
mode `0600`, and keep the data volume and backups private. See `docs/VPS.md`.
