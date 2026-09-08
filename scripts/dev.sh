#!/usr/bin/env bash
set -euo pipefail

root="${DOCMIND_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "$root/scripts/lib/runtime-checks.sh"
check_local_dependencies "$root"
if ! ensure_backend_venv "$root"; then
  "$root/scripts/setup-backend.sh" || die "后端依赖安装失败"
fi
check_port_available 18900 || die "18900 已被其他服务占用"
resolve_electron_exec "$root"
cd "$root"
if [[ "${DOCMIND_DEV_DRY_RUN:-0}" == "1" ]]; then
  echo "desktop:dev"
  exit 0
fi
exec npm run desktop:dev
