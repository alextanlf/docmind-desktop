#!/usr/bin/env bash
#
# 为 macOS 构建图片文字识别小程序（通用二进制：arm64 + x86_64）。
#
# 为什么编译进包而不是把二进制提交进仓库：
#
#   * **架构不能靠"提交一个二进制"覆盖。** 只提交 arm64 的话，Intel Mac 上
#     `FileManager` 仍会认为它存在且可执行，直到真正执行才被系统拒绝（bad CPU type）。
#     这个应用的安装包要同时支持 Apple Silicon 与 Intel，所以必须出通用二进制。
#   * **提交二进制在评审里不可读**，而且换架构时要记得重新提交 —— 那正是会忘掉的事。
#   * 编译代价很低（实测约 2 秒/架构），放在打包流程里不值得省。
#
# 产物落在 backend/app/document/ocr/vision_ocr，随应用包分发。
# 用户机器上**不需要** Xcode：二进制只链系统框架（Vision / AppKit / Foundation）。
#
# 用法：
#   ./scripts/build-vision-ocr.sh          # 构建
#   ./scripts/build-vision-ocr.sh --check  # 只检查产物是否存在且可跑
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/backend/app/document/ocr/vision_ocr.swift"
OUTPUT="$ROOT/backend/app/document/ocr/vision_ocr"

# 部署目标 12.0：Vision 的文字识别在这个版本上可用，且覆盖范围足够老。
MACOS_TARGET="12.0"
ARCHITECTURES=("arm64" "x86_64")

if [[ "${1:-}" == "--check" ]]; then
  if [[ ! -x "$OUTPUT" ]]; then
    echo "缺少识别程序：$OUTPUT" >&2
    echo "请先运行 scripts/build-vision-ocr.sh" >&2
    exit 1
  fi
  "$OUTPUT" --probe >/dev/null
  echo "识别程序可用：$OUTPUT"
  exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "只在 macOS 上构建（当前：$(uname -s)）" >&2
  exit 1
fi

if ! command -v swiftc >/dev/null 2>&1; then
  echo "找不到 swiftc。请安装 Xcode 或 Command Line Tools：xcode-select --install" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

SLICES=()
for ARCH in "${ARCHITECTURES[@]}"; do
  SLICE="$WORK/vision_ocr_$ARCH"
  echo "编译 $ARCH …"
  # 交叉编译在 Apple Silicon 与 Intel 上都能出两个架构：两个平台的 SDK 都带对方的目标。
  swiftc -O -target "${ARCH}-apple-macosx${MACOS_TARGET}" "$SOURCE" -o "$SLICE"
  SLICES+=("$SLICE")
done

if [[ ${#SLICES[@]} -gt 1 ]]; then
  echo "合并为通用二进制 …"
  lipo -create "${SLICES[@]}" -output "$OUTPUT"
else
  cp "${SLICES[0]}" "$OUTPUT"
fi
chmod +x "$OUTPUT"

echo
echo "产物：$OUTPUT"
lipo -info "$OUTPUT" || true
# 立刻探一次：编出来的东西跑不起来时，在构建阶段就失败，而不是等用户导入文档。
"$OUTPUT" --probe >/dev/null
echo "探针通过。"
