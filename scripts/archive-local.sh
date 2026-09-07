#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${DOCMIND_ENABLE_LOCAL_ARCHIVE:-0}" != "1" ]]; then
  echo "OPTIONAL / NOT BUILT"
  exit 0
fi

out="${DOCMIND_ARCHIVE_DIR:-$root/dist-local}"
mkdir -p "$out"
npm run desktop:build
if [[ ! -d "$root/out" ]]; then
  echo "本地归档失败：缺少编译产物" >&2
  exit 1
fi
echo "OPTIONAL / NOT SIGNED: local archive artifacts are available under $out"
