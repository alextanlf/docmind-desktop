#!/usr/bin/env bash
set -euo pipefail

die() { echo "$1" >&2; return 1; }
check_local_dependencies() {
  local root="$1"
  for command_name in node npm uv; do
    command -v "$command_name" >/dev/null 2>&1 || die "缺少命令：$command_name"
  done
  # python3 只用于解析依赖声明这类一次性小脚本；Windows 的 venv 里是
  # python.exe 而非 python3，所以单独按平台探测。
  local python_bin="python3"
  if is_windows_host; then python_bin="python"; fi
  command -v "$python_bin" >/dev/null 2>&1 || die "缺少命令：$python_bin"
  [[ -x "$root/node_modules/.bin/electron" ]] || die "缺少 Electron 依赖，请先运行 npm install"
}
# Git Bash / MSYS2 / Cygwin 下的 uname -s 形如 MINGW64_NT-10.0 / MSYS_NT-*。
is_windows_host() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}
# venv 的入口目录两平台不同：POSIX 是 bin/python，Windows 是 Scripts/python.exe。
backend_venv_python() {
  local root="$1"
  if is_windows_host; then
    echo "$root/backend/.venv/Scripts/python.exe"
  else
    echo "$root/backend/.venv/bin/python"
  fi
}
ensure_backend_venv() { [[ -x "$(backend_venv_python "$1")" ]]; }
check_port_available() {
  local port="$1"
  # lsof 在 Windows 上不存在（Git Bash 也不带），退到 netstat。
  if command -v lsof >/dev/null 2>&1; then
    ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  if command -v netstat >/dev/null 2>&1; then
    ! netstat -ano | grep -qE "[:.]${port}[[:space:]].*LISTENING"
    return
  fi
  die "无法检查端口：缺少 lsof 或 netstat"
}

resolve_electron_exec() {
  local root="$1"
  if [[ -n "${ELECTRON_EXEC_PATH:-}" && -x "$ELECTRON_EXEC_PATH" ]]; then
    return 0
  fi
  if is_windows_host; then
    # Windows 的 Electron 运行时是 dist/electron.exe，没有 .app 包结构。
    if [[ -x "$root/node_modules/electron/dist/electron.exe" ]]; then
      return 0
    fi
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
