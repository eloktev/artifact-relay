# Contributing

Thank you for improving Artifact Relay.

1. Open an issue before a large or behavior-changing contribution.
2. Fork the repository and create a focused branch from the current default branch.
3. Add a failing test first for behavior changes, then implement the smallest fix.
4. Do not commit `.env`, credentials, artifact data, generated backups, or private URLs.
5. Run the complete local gate:

   ```sh
   uv sync --frozen --extra dev
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy
   uv run pytest
   docker build -t artifact-relay:contrib .
   ./scripts/smoke.sh artifact-relay:contrib 18080
   ```

6. Update documentation and `THIRD_PARTY_NOTICES.md` when adding vendored assets.
7. Submit a pull request that explains the problem, approach, security impact, and
   verification performed.

By contributing, you agree that your contribution is licensed under the MIT
License in `LICENSE`. Report vulnerabilities privately as described in
`SECURITY.md`, not in an issue or pull request.
