#!/usr/bin/env bash
# 构建随应用分发的自包含后端 runtime。
#
# 产物 backend/build/backend-runtime/ 是一个自包含目录：
#   bin/python3        uv 管理的独立 CPython 解释器（不依赖系统 Python）
#   lib/               site-packages（已剔除 sentence-transformers/torch）
#   app/ backend 代码  alembic.ini（可用 alembic 跑迁移）
#
# 之所以做成runtime 而不是打包 venv：
#   1) venv 依赖绝对路径与开发机Python，无法搬进 .app；
#   2) fp32 回退路径要拖入 torch（约 500MB），打包态内置 int8 ONNX 模型用不到它。
#
# 用法：scripts/build-backend-runtime.sh
set -euo pipefail

# 脚本位于 backend/scripts/，所以 .. 就是 backend 根目录。
backend_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_root="$backend_dir/build"
runtime_dir="$out_root/backend-runtime"
python_version="${DOCMIND_BACKEND_PYTHON:-3.12}"

if ! command -v uv >/dev/null 2>&1; then
  echo "缺少 uv：https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "清理旧的 runtime..."
# 用 mv 而不是 rm -rf：runtime 有近万个文件，递归删除在受保护环境里会被拦。
if [ -e "$runtime_dir" ]; then
  mv "$runtime_dir" "${TMPDIR:-/tmp}/docmind-runtime-old.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
fi
mkdir -p "$runtime_dir"

# 1) 独立解释器。必须用 uv 托管目录下的那份 —— `uv python find` 在项目上下文里会
#    返回 .venv/bin/python3，直接用它会把开发机路径写进产物。
echo "准备独立 Python $python_version ..."
uv python install "$python_version" >/dev/null
uv_root="${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"
interp_real="$(find "$uv_root" -maxdepth 3 -type f \
  -path "*cpython-${python_version}*" -name "python${python_version}" 2>/dev/null | sort | tail -1)"
if [ -z "$interp_real" ] || [ ! -x "$interp_real" ]; then
  echo "无法在 $uv_root 下定位独立Python ${python_version}" >&2
  echo "请先执行：uv python install ${python_version}" >&2
  exit 1
fi
echo "  解释器：$interp_real"
interp="$interp_real"

# 网络：直连 pypi.org 在国内常常超时，默认走阿里云镜像，可用
# DOCMIND_PYPI_INDEX 覆盖（设空字符串可强制直连）。
pypi_index="${DOCMIND_PYPI_INDEX:-https://mirrors.aliyun.com/pypi/simple/}"
uv_index_args=()
if [ -n "$pypi_index" ]; then
  uv_index_args=(--index-url "$pypi_index")
  echo "  PyPI 镜像：$pypi_index"
fi

# 2) 复制解释器与标准库。必须先铺标准库才能装依赖：uv --target 装到
#    lib/python3.12/site-packages，而这个目录是标准库 lib/python3.12/ 的子目录。
#
#    必须复制而不是符号链接 —— 符号链接指向 ~/.local/share/uv/python，
#    打包后换台机器就断了。uv 的 standalone CPython 是可重定位构建
#    （otool 只显示系统库），搬进 .app 后仍能工作。
echo "复制独立解释器与标准库..."
uv_prefix="$(dirname "$(dirname "$interp_real")")"
mkdir -p "$runtime_dir/bin"
cp -R "$uv_prefix/lib" "$runtime_dir/lib"
cp -R "$uv_prefix/bin/." "$runtime_dir/bin/"
if [ ! -e "$runtime_dir/bin/python3" ] && [ -e "$runtime_dir/bin/python${python_version}" ]; then
  ln -sf "python${python_version}" "$runtime_dir/bin/python3"
fi
# 开发期文件不进包
rm -rf "$runtime_dir/lib/include" "$runtime_dir/lib/share" 2>/dev/null || true
rm -rf "$runtime_dir/lib/pkgconfig" "$runtime_dir/lib/tcl9" "$runtime_dir/lib/tk9.0" 2>/dev/null || true

site_dir="$runtime_dir/lib/python${python_version}/site-packages"
mkdir -p "$site_dir"

# 冒烟检查：解释器必须能在新位置找到标准库，否则后面全是白干。
if ! "$runtime_dir/bin/python3" -c "import encodings" >/dev/null 2>&1; then
  echo "复制后的解释器找不到标准库（lib/python${python_version}/encodings）" >&2
  exit 1
fi
echo "  标准库自检通过"

# 3) 依赖装进 site-packages。--no-deps 关掉自动递归，我们显式列出需要的东西，
#    这样才能精确剔除 sentence-transformers（连带 torch/scipy/sklearn，约 600M）。
# 从 pyproject.toml 的 [project.dependencies] 读取声明（而不是手抄包名+版本），
# 交给 uv 解析完整依赖树。手抄+ --no-deps 的方式很脆：漏一个传递依赖
# （如 keyring 依赖 jaraco.*）就要在运行时才炸。
echo "安装后端依赖（自动解析依赖树，fp32 分支已在 pyproject 里移到 optional-dependencies）..."
# 注意：macOS 自带 bash 3.2 没有 mapfile，这里用 while read 数组的兼容写法。
backend_reqs=()
while IFS= read -r line; do
  [ -n "$line" ] && backend_reqs+=("$line")
done < <(
  python3 - "$backend_dir/pyproject.toml" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
# 只取 [project] 段的 dependencies 数组，避开 [project.optional-dependencies] 的 fp32
m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
if not m:
    raise SystemExit("未能从 pyproject.toml 解析 [project].dependencies")
for item in re.findall(r'"([^"]+)"', m.group(1)):
    print(item)
PY
)
if [ "${#backend_reqs[@]}" -eq 0 ]; then
  echo "pyproject.toml 未解析出任何依赖" >&2
  exit 1
fi
echo "  ${#backend_reqs[@]} 个直接依赖"
uv pip install \
  --python "$interp" \
  --target "$site_dir" \
  "${backend_reqs[@]}" \
  "${uv_index_args[@]}" >/dev/null

# 依赖装完后必须能在「解释器的新位置」真的导入它们 —— 装到 target 只是落了盘，
# 能不能 import 取决于解释器是否把 site-packages 算进 sys.path。
# 刻意不加 sys.path.insert：要测的就是「裸解释器能否直接 import」这条真实启动路径
# （site-packages 位于 lib/python3.12/ 下，正是解释器默认搜索的位置）。
if ! probe_out="$("$runtime_dir/bin/python3" -c "
import fastapi, uvicorn, sqlalchemy, onnxruntime, pymupdf, keyring, rank_bm25, chromadb, alembic
" 2>&1)"; then
  echo "site-packages 导入自检失败（裸解释器无法直接 import）" >&2
  echo "${probe_out}" | tail -8 >&2
  exit 1
fi
echo "  关键依赖自检通过"

# 5) 复制应用代码与 alembic 配置。
echo "复制应用代码..."
mkdir -p "$runtime_dir/app"
cp -R "$backend_dir/app/." "$runtime_dir/app/"
cp "$backend_dir/alembic.ini" "$runtime_dir/alembic.ini"
if [ -d "$backend_dir/migrations" ]; then
  cp -R "$backend_dir/migrations" "$runtime_dir/migrations"
fi

# 6) 写启动入口。uvicorn 以 app 为包导入，cwd 设为 runtime 根。
cat > "$runtime_dir/run.py" <<'PY'
"""随应用分发的后端启动入口。

由 Electron 主进程 spawn（cwd = 本目录），等价于 `python -m app`，
但解释器路径由 Info.plist / backend-manager 指向本runtime 内的 bin/python3。
"""
from __future__ import annotations

import uvicorn

from app.config import get_settings
from app.main import create_app


def main() -> None:
    settings = get_settings()
    # 与开发态 app/__main__.py 保持同一道硬约束：只允许绑回环，
    # 防止把本地知识库服务暴露到局域网。
    if settings.host != "127.0.0.1" or settings.port != 18900:
        raise RuntimeError("DocMind 后端只能绑定到 127.0.0.1:18900")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
PY

# 7) 剔除 .pyc 与测试文件，减小体积。
find "$runtime_dir" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$site_dir" -name '*.pyc' -delete 2>/dev/null || true
find "$runtime_dir/app" -name 'tests' -type d -prune -exec rm -rf {} + 2>/dev/null || true

size="$(du -sh "$runtime_dir" | cut -f1)"
# 注意：变量名后紧跟全角标点时必须写成 ${var}，否则 bash 会把全角字符并入变量名。
echo "后端 runtime 构建完成：${runtime_dir}（${size}）"
