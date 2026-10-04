#!/usr/bin/env bash
# 构建随应用分发的自包含后端 runtime。
#
# 产物 backend/build/backend-runtime/ 是一个自包含目录：
#   POSIX（mac/Linux）：
#     bin/python3        uv 管理的独立 CPython 解释器（不依赖系统 Python）
#     lib/python3.12/    标准库 + site-packages（已剔除 sentence-transformers/torch）
#   Windows：
#     python.exe         uv 托管的 install-only 布局，exe 必须与 Lib/ 同级
#     Lib/               标准库 + site-packages
#   两平台共有：app/（后端代码）、alembic.ini、run.py（启动入口）
#
# 之所以做成 runtime 而不是打包 venv：
#   1) venv 依赖绝对路径与开发机 Python，无法搬进安装包；
#   2) fp32 回退路径要拖入 torch（约 500MB），打包态内置 int8 ONNX 模型用不到它。
#
# Windows 上需在 Git Bash / MSYS2 里执行（脚本是 bash，且用到 `cp -R` / `find`）。
#
# 用法：scripts/build-backend-runtime.sh
set -euo pipefail

# 脚本位于 backend/scripts/，所以 .. 就是 backend 根目录。
backend_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_root="$backend_dir/build"
runtime_dir="$out_root/backend-runtime"
python_version="${DOCMIND_BACKEND_PYTHON:-3.12}"

# ── 平台判定 ────────────────────────────────────────────────────────────────
# uname -s 在 Git Bash / MSYS2 / Cygwin 下分别返回 MINGW*_NT-* / MSYS_NT-* /
# CYGWIN_NT-*。这决定了两件必须不同的事：
#   1) 解释器文件名（python3.12 vs python.exe）与所在层级（bin/ vs 顶层）；
#   2) 标准库目录名（lib/python3.12/ vs Lib/）—— 装错位置的 site-packages
#      不会被裸解释器算进 sys.path，第 4 步的自检会失败。
os_name="$(uname -s)"
case "$os_name" in
  MINGW*|MSYS*|CYGWIN*) is_windows=1 ;;
  *) is_windows=0 ;;
esac
platform_label="POSIX"
if [ "$is_windows" = 1 ]; then platform_label="Windows"; fi

if ! command -v uv >/dev/null 2>&1; then
  echo "缺少 uv：https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "平台：${os_name}（${platform_label}）"
echo "清理旧的 runtime..."
# 用 mv 而不是 rm -rf：runtime 有近万个文件，递归删除在受保护环境里会被拦。
# Windows 上 TMPDIR 可能未设置，退回 TEMP。
tmp_root="${TMPDIR:-${TEMP:-/tmp}}"
if [ -e "$runtime_dir" ]; then
  mv "$runtime_dir" "${tmp_root}/docmind-runtime-old.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
fi
mkdir -p "$runtime_dir"

# 1) 独立解释器。必须用 uv 托管目录下的那份 —— `uv python find` 在项目上下文里会
#    返回 .venv/bin/python3，直接用它会把开发机路径写进产物。
#
#    uv 的目录布局两平台不同：
#      POSIX：   cpython-3.12.x-<target>/bin/python3.12
#      Windows： cpython-3.12.x-windows-x86_64-none/python.exe   （install-only）
#    所以文件名和搜索深度都要分平台。
echo "准备独立 Python $python_version ..."
uv python install "$python_version" >/dev/null
uv_root="${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"
if [ "$is_windows" = 1 ]; then
  interp_name="python.exe"
  search_depth=2
else
  interp_name="python${python_version}"
  search_depth=3
fi
interp_real="$(find "$uv_root" -maxdepth "$search_depth" -type f \
  -path "*cpython-${python_version}*" -name "$interp_name" 2>/dev/null | sort | tail -1)"
if [ -z "$interp_real" ] || [ ! -x "$interp_real" ]; then
  echo "无法在 ${uv_root} 下定位独立 Python ${python_version}（${interp_name}）" >&2
  echo "请先执行：uv python install ${python_version}" >&2
  exit 1
fi
echo "  解释器：${interp_real}"
interp="$interp_real"

# 网络：直连 pypi.org 在国内常常超时，默认走阿里云镜像，可用
# DOCMIND_PYPI_INDEX 覆盖（设空字符串可强制直连）。
pypi_index="${DOCMIND_PYPI_INDEX:-https://mirrors.aliyun.com/pypi/simple/}"
uv_index_args=()
if [ -n "$pypi_index" ]; then
  uv_index_args=(--index-url "$pypi_index")
  echo "  PyPI 镜像：${pypi_index}"
fi

# 2) 复制解释器与标准库。必须先铺标准库才能装依赖：uv --target 装到
#    site-packages，而这个目录必须是标准库目录的子目录才能被裸解释器识别。
#
#    必须复制而不是符号链接 —— 符号链接指向 ~/.local/share/uv/python，
#    打包后换台机器就断了。uv 的 standalone CPython 是可重定位构建
#    （otool 只显示系统库），搬进安装包后仍能工作。
#    Windows 另有一层约束：exe 靠同级目录定位 Lib/，不能重命名也不能挪走，
#    所以整个 uv_prefix 目录原样铺到 runtime 根。
echo "复制独立解释器与标准库..."
uv_prefix="$(dirname "$(dirname "$interp_real")")"
if [ "$is_windows" = 1 ]; then
  # install-only 布局：cpython-3.12.x-.../ 下就是 python.exe + Lib/ + DLLs/，
  # 整体铺到 runtime 根，解释器保持在顶层。
  cp -R "$uv_prefix/." "$runtime_dir/"
  runtime_python="$runtime_dir/python.exe"
  site_dir="$runtime_dir/Lib/site-packages"
  # 开发期文件不进包
  rm -rf "$runtime_dir/include" 2>/dev/null || true
  rm -rf "$runtime_dir/tcl" 2>/dev/null || true
else
  mkdir -p "$runtime_dir/bin"
  cp -R "$uv_prefix/lib" "$runtime_dir/lib"
  cp -R "$uv_prefix/bin/." "$runtime_dir/bin/"
  # python3 是给 main 进程探测用的稳定名字（见 backend-runtime-contract.ts
  # 的 RUNTIME_EXECUTABLE_CANDIDATES）。符号链接在此处是安全的：指向的是
  # 同一个 runtime 目录内的相对名，打包后不依赖任何外部路径。
  if [ ! -e "$runtime_dir/bin/python3" ] && [ -e "$runtime_dir/bin/python${python_version}" ]; then
    ln -sf "python${python_version}" "$runtime_dir/bin/python3"
  fi
  # 开发期文件不进包
  rm -rf "$runtime_dir/lib/include" "$runtime_dir/lib/share" 2>/dev/null || true
  rm -rf "$runtime_dir/lib/pkgconfig" "$runtime_dir/lib/tcl9" "$runtime_dir/lib/tk9.0" 2>/dev/null || true
  runtime_python="$runtime_dir/bin/python3"
  site_dir="$runtime_dir/lib/python${python_version}/site-packages"
fi
mkdir -p "$site_dir"

# 冒烟检查：解释器必须能在新位置找到标准库，否则后面全是白干。
if ! "$runtime_python" -c "import encodings" >/dev/null 2>&1; then
  echo "复制后的解释器找不到标准库（site_dir=${site_dir}）" >&2
  exit 1
fi
echo "  标准库自检通过"

# 3) 依赖装进 site-packages。--no-deps 关掉自动递归，我们显式列出需要的东西，
#    这样才能精确剔除 sentence-transformers（连带 torch/scipy/sklearn，约 600M）。
# 从 pyproject.toml 的 [project.dependencies] 读取声明（而不是手抄包名+版本），
# 交给 uv 解析完整依赖树。手抄 + --no-deps 的方式很脆：漏一个传递依赖
# （如 keyring 依赖 jaraco.*）就要在运行时才炸。
# 注意：--python 指向 uv 托管的原始解释器，不是 runtime 里的副本 —— uv 只需要
# 一个能跑的解释器来解析/下载 wheel，落盘位置由 --target 决定。
echo "安装后端依赖（自动解析依赖树，fp32 分支已在 pyproject 里移到 optional-dependencies）..."
# 注意：macOS 自带 bash 3.2 没有 mapfile，这里用 while read 数组的兼容写法。
backend_reqs=()
while IFS= read -r line; do
  [ -n "$line" ] && backend_reqs+=("$line")
done < <(
  "$runtime_python" - "$backend_dir/pyproject.toml" <<'PY'
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
# （site-packages 位于解释器默认搜索的位置下）。
#
# keyring 的 Windows 后端需要 pywin32-ctypes（keyring 自带 win32 marker 会自动装上）。
# 这里显式导入一次，把「构建期就发现缺依赖」变成硬失败，而不是等到用户点保存 API Key
# 才在运行期看到「未配置」。
probe_imports="import fastapi, uvicorn, sqlalchemy, onnxruntime, pymupdf, keyring, rank_bm25, chromadb, alembic"
if [ "$is_windows" = 1 ]; then
  probe_imports="${probe_imports}, win32ctypes.pywin32"
fi
if ! probe_out="$("$runtime_python" -c "$probe_imports" 2>&1)"; then
  echo "site-packages 导入自检失败（裸解释器无法直接 import）" >&2
  echo "${probe_out}" | tail -8 >&2
  exit 1
fi
echo "  关键依赖自检通过"

# 4) 复制应用代码与 alembic 配置。
echo "复制应用代码..."
mkdir -p "$runtime_dir/app"
cp -R "$backend_dir/app/." "$runtime_dir/app/"
cp "$backend_dir/alembic.ini" "$runtime_dir/alembic.ini"
if [ -d "$backend_dir/migrations" ]; then
  cp -R "$backend_dir/migrations" "$runtime_dir/migrations"
fi

# 5) 写启动入口。uvicorn 以 app 为包导入，cwd 设为 runtime 根。
#    Electron 主进程 spawn 本文件（cwd = runtime 根），等价于 `python -m app`，
#    但解释器由 main 进程从 resourcesPath 解析（见 backend-runtime-contract.ts）。
cat > "$runtime_dir/run.py" <<'PY'
"""随应用分发的后端启动入口。

由 Electron 主进程 spawn（cwd = 本目录），等价于 `python -m app`，
但解释器路径由 main 进程从 process.resourcesPath 解析。
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

# 6) 剔除 .pyc 与测试文件，减小体积。
find "$runtime_dir" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$site_dir" -name '*.pyc' -delete 2>/dev/null || true
find "$runtime_dir/app" -name 'tests' -type d -prune -exec rm -rf {} + 2>/dev/null || true

size="$(du -sh "$runtime_dir" 2>/dev/null | cut -f1 || echo '?')"
# 注意：变量名后紧跟全角标点时必须写成 ${var}，否则 bash 会把全角字符并入变量名。
echo "后端 runtime 构建完成：${runtime_dir}（${size}）"
if [ "$is_windows" = 1 ]; then
  echo "  解释器入口：python.exe（与 Lib/ 同级，不可重命名）"
else
  echo "  解释器入口：bin/python3"
fi
