#!/usr/bin/env bash
set -euo pipefail

die() { echo "$1" >&2; return 1; }
check_local_dependencies() {
  local root="$1"
  for command_name in node npm python3 uv; do
    command -v "$command_name" >/dev/null 2>&1 || die "缺少命令：$command_name"
  done
  [[ -x "$root/node_modules/.bin/electron" ]] || die "缺少 Electron 依赖，请先运行 npm install"
}
ensure_backend_venv() { [[ -x "$1/backend/.venv/bin/python" ]]; }
check_port_available() {
  command -v lsof >/dev/null 2>&1 || die "无法检查端口：缺少 lsof"
  ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}
