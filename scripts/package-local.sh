#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
electron_app="${DOCMIND_ELECTRON_APP:-$HOME/Applications/Electron.app}"
out_app="${DOCMIND_APP_OUT:-/Applications/DocMind.app}"
backend_dir="$root/backend"
backend_python="$backend_dir/.venv/bin/python"
electron_dist="$root/build/electron-dist"

if [[ ! -d "$electron_app" ]]; then
  echo "缺少 Electron 运行时：$electron_app" >&2
  exit 1
fi
if [[ ! -x "$backend_python" ]]; then
  echo "缺少后端解释器：$backend_python" >&2
  exit 1
fi

if [[ ! -x "$(command -v node)" ]]; then
  echo "缺少 node" >&2
  exit 1
fi

electron_version="$(node -p "require('./node_modules/electron/package.json').version")"

echo "构建渲染/主进程产物..."
npm run desktop:build >/dev/null

echo "准备本地 Electron 分发目录..."
mkdir -p "$electron_dist"
ln -sfn "$electron_app" "$electron_dist/Electron.app"
printf '%s\n' "$electron_version" > "$electron_dist/version"

echo "使用 electron-builder 生成应用包..."
DOCMIND_BACKEND_COMMAND="$backend_python" \
DOCMIND_BACKEND_CWD="$backend_dir" \
DOCMIND_BACKEND_ARGS='["-m", "app"]' \
  npx electron-builder \
    --config "$root/electron-builder.config.js" \
    --mac dir \
    --publish never

built_app="$root/dist/mac-arm64/DocMind.app"
if [[ ! -d "$built_app" ]]; then
  echo "构建输出不存在：$built_app" >&2
  exit 1
fi

echo "安装到 $out_app ..."
if [[ -d "$out_app" ]]; then
  backup="$HOME/.Trash/DocMind.app.$(date +%Y%m%d%H%M%S)"
  mkdir -p "$HOME/.Trash"
  mv "$out_app" "$backup"
fi
ditto "$built_app" "$out_app"

echo "打包完成：$out_app"
