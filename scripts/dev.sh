#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v node >/dev/null
command -v python3 >/dev/null
command -v uv >/dev/null
if [[ ! -x "$root/backend/.venv/bin/python" ]]; then
  "$root/scripts/setup-backend.sh"
fi
cd "$root"
exec npm run desktop:dev
