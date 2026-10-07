#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
uv run pytest -v
uv run ruff check app tests
# Plugins sit outside backend/ and would otherwise go unlinted.
uv run ruff check --config pyproject.toml ../plugins
cd "$root"
npm run desktop:build
exec node scripts/verify-desktop-build.mjs
