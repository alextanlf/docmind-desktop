#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
uv run pytest -v
uv run ruff check app tests
cd "$root"
npm run desktop:test
npm run typecheck
npm run lint
npm run format:check
npm run test:e2e
