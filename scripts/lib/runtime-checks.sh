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

resolve_electron_exec() {
  local root="$1"
  if [[ -n "${ELECTRON_EXEC_PATH:-}" && -x "$ELECTRON_EXEC_PATH" ]]; then
    return 0
  fi
  if [[ -x "$root/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron" ]]; then
    return 0
  fi
  local candidate
  for candidate in \
    "${HOME:-}/Applications/Electron.app/Contents/MacOS/Electron" \
    "/Applications/Electron.app/Contents/MacOS/Electron"
  do
    if [[ -x "$candidate" ]]; then
      export ELECTRON_EXEC_PATH="$candidate"
      return 0
    fi
  done
}
