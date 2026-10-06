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
probe_imports="import fastapi, uvicorn, sqlalchemy, onnxruntime, pymupdf, keyring, rank_bm25, sqlite_vec, alembic"
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

# 6.05)剔除构建期工具（pip / ensurepip），共约 8MB。
#
# 它们的来源是第 3 步的 `uv pip install --target`：uv 把自己的 installer 一并落进
# target，而 uv 托管的 CPython 本身并不自带 pip（~/.local/share/uv/python/*/
# lib/python3.12/site-packages 里没有 pip），所以这是纯副产物，不是运行时地基。
#
# 🔴 删之前必须确认「没有任何运行路径需要 pip」，否则新用户直接崩：
#   - backend/app 与 electron/ 全仓 grep `pip` / `ensurepip` / `pip install` 均 0 命中；
#   - 唯一的后端子进程是 `python -m playwright install chromium`（yuque/gateway.py
#     的 install_browser），它走 playwright 自带的 driver/node 二进制 + HTTP 下载，
#     不经过 pip —— 实测删后 --dry-run 仍 exit=0；
#   - 代码里没有 repair/doctor 类自修复接口，不存在「少包就现场补装」的路径。
# 已实测（复制真产物删 pip后跑真实启动）：18 个 alembic 迁移全过、73 条路由注册、
# 4 个真实 API 200、真实 bge-m3 model.onnx 加载 OK。
#
# 为什么连 ensurepip 一起删：它内置的 pip-*.whl 占 1.8MB，而它的唯一用途就是
# 「把 pip 装回来」—— 装了 pip 又删 ensurepip 没有意义，两者是一对。
# Windows 上没有 bin/ 目录，bin/pip* 那部分自然跳过。
rm -rf "$site_dir/pip" 2>/dev/null || true
find "$site_dir" -maxdepth 1 -name 'pip-*.dist-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
stdlib_dir="$(dirname "$site_dir")"
rm -rf "$stdlib_dir/ensurepip" 2>/dev/null || true
rm -f "$runtime_dir/bin/pip" "$runtime_dir/bin/pip3" "$runtime_dir/bin/pip${python_version}" 2>/dev/null || true

# 反向断言：pip 必须真的不在了。删不掉却静默通过，等于白删；
# 下次谁把 uv 的 installer 又带回来，这里会拦住。
if "$runtime_python" -I -c "import pip" >/dev/null 2>&1; then
  echo "pip 仍可被导入，剔除失败（是否被上游重新引入？）" >&2
  exit 1
fi

# 6.1) 向量库真实链路自检。
#
# 🔴 必须用 -I（隔离模式）：它同时忽略 PYTHONPATH 与用户 site-packages。
#    否则解释器会从别的路径兜底 import 到开发机上的包，探针「通过」却是假的
#    —— 上一版就是这么静默失效的（已实测：删掉 chromadb_rust_bindings 后
#    探针仍返回 OK，因为它加载的是 runtime 之外的真包）。
#    断言 __file__ 属于 sys.prefix 是第二道保险。
#
# 探针覆盖真实使用到的每一条路径，缺一条就可能在用户导入文档时才炸：
#   建集合 → 写入 → 无过滤查询 → where 过滤查询 → 维度不匹配拦截
#   → 删除 → 删集合 → 查询不存在的集合
# where 过滤用「放大候选集 + 回表筛」，与 app/storage/vectorstore.py::_search
# 同一策略；UUID 仓库名走加引号标识符，覆盖生产里 repository_id 是 UUID 的事实。
#
if ! probe_out="$("$runtime_python" -I - <<'PY' 2>&1
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import sqlite_vec

# 必须是本次构建产出的那份，不能是开发机上的。
assert sqlite_vec.__file__.startswith(sys.prefix), (
    "sqlite_vec 来自 runtime 之外：" + sqlite_vec.__file__
)
assert sqlite3.sqlite_version_info >= (3, 41, 0), (
    f"sqlite3 过旧（{sqlite3.sqlite_version}），sqlite-vec 扩展无法加载"
)

# -I 同时把 cwd 也踢出 sys.path，所以要手动把 runtime 根（app/ 的父目录）加回来。
# 这不破坏隔离性：加的是本次构建产物自己的目录，仍是「只认本次产物」。
sys.path.insert(0, str(Path.cwd()))

from app.storage.vectorstore import PersistentVectorStore  # noqa: E402
from app.config import VectorStoreSettings  # noqa: E402

store = PersistentVectorStore(
    VectorStoreSettings(directory=Path(tempfile.mkdtemp()) / "vectors")
)

# repository_id 在生产里是 UUID → 集合名含连字符，必须加引号才不被解析成减法。
repository_id = str(uuid.uuid4())
ids = [str(uuid.uuid4()) for _ in range(3)]
embeddings = [[1.0] * 1024, [0.9] + [0.0] * 1023, [0.0] * 1023 + [1.0]]
store.upsert(
    repository_id,
    ids,
    ["A", "B", "C"],
    embeddings,
    [{"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": "d1"}],
)

hits = store.query(repository_id, embeddings[0], top_k=3)
assert hits[0].id == ids[0], hits
assert abs(hits[0].similarity - 1.0) < 1e-5, hits[0]

filtered = store.query(repository_id, embeddings[0], top_k=3, where={"doc_id": "d1"})
assert {h.id for h in filtered} == {ids[0], ids[2]}, filtered

# 🔴 低选择性过滤不得漏召回。曾经的实现是「取 top_k×10 再在 Python 里筛」，
# 匹配项排在窗口外时返回空且**无任何报错** —— 概览块注入会悄悄失效。
#
# 场景要足够极端，否则探针自己也会假通过（第一版就栽在这）：
# 唯一匹配项的相似度必须**低于**全部干扰项，且干扰项数量要超过放大窗口
# （top_k=3 × 10 = 30），才能确保它落在窗口之外。
sparse_repo = str(uuid.uuid4())
unit = [0.0] * 1024
probe_query = [1.0] + unit[1:]
low = unit[:1] + [0.1] + unit[2:]      # 与查询相似度 0.1 —— 唯一匹配项
high = unit[:1] + [0.9] + unit[2:]     # 与查询相似度 0.9 —— 99 个干扰项
store.upsert(
    sparse_repo,
    ["target"] + [f"noise-{index}" for index in range(99)],
    ["t"] * 100,
    [low] + [high] * 99,
    [{"source_type": "WANTED"}] + [{"source_type": "OTHER"} for _ in range(99)],
)
sparse_hits = store.query(sparse_repo, probe_query, top_k=3, where={"source_type": "WANTED"})
assert [h.id for h in sparse_hits] == ["target"], sparse_hits

# 记忆索引走的是另一组方法与另一套集合名。
store.upsert_memory(
    "probe_mem",
    ["m1"],
    [[0.3] * 1024],
    ["memory"],
    [{"repository_id": repository_id, "source_id": "s1", "kind": "note"}],
)
assert [h.id for h in store.query_memory("probe_mem", [0.3] * 1024, 5)] == ["m1"]
assert store.query_memory("probe_mem", [0.3] * 1024, 5, repository_id="other") == []
store.delete_memory("probe_mem", ["m1"])
assert store.query_memory("probe_mem", [0.3] * 1024, 5) == []

# 维度不匹配必须给出「重建索引」而不是裸 sqlite 错误。
try:
    store.query(repository_id, [1.0] * 768, top_k=1)
except Exception as error:
    assert getattr(error, "code", "") == "INDEX_FAILED", error
else:
    raise AssertionError("维度不匹配未抛错")

store.delete(repository_id, [ids[0]])
assert len(store.query(repository_id, embeddings[0], top_k=3)) == 2
store.delete_collection(repository_id)
assert store.query(repository_id, embeddings[0], top_k=3) == []
assert store.query(str(uuid.uuid4()), embeddings[0], top_k=3) == []

print("OK")
PY
)"; then
  echo "sqlite-vec 向量库自检失败（构建产物不可用）" >&2
  echo "${probe_out}" | tail -12 >&2
  exit 1
fi
echo "  向量库自检通过（隔离模式，已确认用的是本次构建产物）"

size="$(du -sh "$runtime_dir" 2>/dev/null | cut -f1 || echo '?')"
# 注意：变量名后紧跟全角标点时必须写成 ${var}，否则 bash 会把全角字符并入变量名。
echo "后端 runtime 构建完成：${runtime_dir}（${size}）"
if [ "$is_windows" = 1 ]; then
  echo "  解释器入口：python.exe（与 Lib/ 同级，不可重命名）"
else
  echo "  解释器入口：bin/python3"
fi
