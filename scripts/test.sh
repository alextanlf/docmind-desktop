#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${DOCMIND_ELECTRON_PATH:-}" ]]; then
  for candidate in \
    "$HOME/Applications/Electron.app/Contents/MacOS/Electron" \
    "$root/build/electron-dist/Electron.app/Contents/MacOS/Electron"; do
    if [[ -x "$candidate" ]]; then
      export DOCMIND_ELECTRON_PATH="$candidate"
      break
    fi
  done
fi

cd "$root/backend"
uv run pytest -v
uv run ruff check app tests
cd "$root"
npm run desktop:test
npm run typecheck
npm run lint
npm run format:check
npm run desktop:build
npm run test:e2e
