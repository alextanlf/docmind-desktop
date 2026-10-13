#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
uv sync --group dev

# 图片文字识别小程序（仅 macOS）：仓库存的是 .swift 源码，二进制按需编译。
# 不编译也能跑，只是图片文字识别不可用；这里编出来，免得开发时以为是缺陷。
if [[ "$(uname -s)" == "Darwin" ]]; then
  cd "$root"
  ./scripts/build-vision-ocr.sh || {
    echo "警告：识别程序编译失败，图片文字识别将不可用（其余功能不受影响）" >&2
  }
fi
