#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
electron_app="${DOCMIND_ELECTRON_APP:-$HOME/Applications/Electron.app}"
out_app="${DOCMIND_APP_OUT:-$HOME/Applications/DocMind.app}"
backend_dir="$root/backend"
backend_python="$backend_dir/.venv/bin/python"

if [[ ! -d "$electron_app" ]]; then
  echo "缺少 Electron 运行时：$electron_app" >&2
  exit 1
fi
if [[ ! -x "$backend_python" ]]; then
  echo "缺少后端解释器：$backend_python" >&2
  exit 1
fi

echo "构建渲染/主进程产物..."
npm run desktop:build >/dev/null

echo "打包 $out_app ..."
rm -rf "$out_app"
cp -R "$electron_app" "$out_app"

app_resources="$out_app/Contents/Resources/app"
mkdir -p "$app_resources/out"
printf '{"name":"docmind-desktop","main":"out/main/index.js"}\n' > "$app_resources/package.json"
cp -R "$root/out/." "$app_resources/out/"

info_plist="$out_app/Contents/Info.plist"
python3 - "$info_plist" "$backend_python" "$backend_dir" <<'PY'
import plistlib
import sys

path, backend_python, backend_dir = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "rb") as handle:
    plist = plistlib.load(handle)
plist["CFBundleName"] = "DocMind"
plist["CFBundleDisplayName"] = "DocMind"
plist["CFBundleIdentifier"] = "local.docmind"
plist["LSEnvironment"] = {
    "DOCMIND_BACKEND_COMMAND": backend_python,
    "DOCMIND_BACKEND_ARGS": '["-m", "app"]',
    "DOCMIND_BACKEND_CWD": backend_dir,
}
with open(path, "wb") as handle:
    plistlib.dump(plist, handle)
PY

codesign --force --deep --sign - "$out_app" >/dev/null 2>&1 || true

echo "打包完成：$out_app"
