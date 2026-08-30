# Artifact Relay — Product Brief

## Goal

Provide a small, self-hosted, single-user service that turns Markdown or standalone HTML into
polished, mobile-first web pages. Automated publishers receive one opaque URL; readers log in
once with an independent password. Optional scoped share links expose one rendered artifact
without exposing the private library or publisher API.

## Core workflow

1. A trusted client publishes content and optional assets with a bearer token.
2. The API returns an unguessable artifact URL.
3. An unauthenticated request sees only preview metadata and a login form.
4. A viewer session opens rendered content and the private library.
5. Artifacts expire automatically unless pinned; the publisher can delete them immediately.

## Security and operations

- Publisher and viewer credentials are independent.
- Markdown is sanitized; standalone HTML runs in a sandboxed opaque-origin iframe.
- Payloads, login attempts, and concurrent Argon2 verification memory are bounded.
- Secrets are generated during bootstrap and never stored as plaintext passwords.
- Local Compose binds only to loopback and disables share links by default.
- VPS deployment terminates TLS at Caddy, uses Secure cookies, and keeps the app on loopback.
- SQLite metadata and artifact files persist in one volume with backup/restore procedures.
- Images, CI actions, and vendored assets have pinned provenance.

## Non-goals

Multi-user accounts, public indexing, collaborative editing, comments, object storage, and
server-side execution of artifact code are outside the initial scope.
