#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/scripts/lib/runtime-checks.sh"
check_local_dependencies "$root"
if ! ensure_backend_venv "$root"; then
  "$root/scripts/setup-backend.sh" || die "后端依赖安装失败"
fi
check_port_available 18900 || die "18900 已被其他服务占用"
cd "$root"
exec npm run desktop:dev
