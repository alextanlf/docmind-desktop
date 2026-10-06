"""打包依赖契约：build-backend-runtime.sh 只解析 [project].dependencies。

这个脚本用非贪婪正则从 pyproject.toml 里截依赖列表，然后交给 uv 解析依赖树。
两个后果必须由测试守住，否则只会在用户点「加载模型」时才炸：

1. **源码 import 的包必须显式声明**。延迟 import 逃过了 grep，构建期探针
   如果没覆盖到就会静默漏过。transformers 就是这么漏的：它只是开发 venv 里
   sentence-transformers 的传递依赖，打包只装直接依赖，于是安装版内置模型
   点击加载必然 ModuleNotFoundError。
2. **dependencies 段的注释里不能出现右方括号**。非贪婪正则 `.*?]` 会被注释里
   的 `]` 提前截断，其后的依赖静默丢失——不报错、不告警。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

# 与 scripts/build-backend-runtime.sh 中的解析逻辑保持一致。
_DEPS_PATTERN = re.compile(r"^dependencies\s*=\s*\[(.*?)\]", re.S | re.M)


def declared_dependencies() -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = _DEPS_PATTERN.search(text)
    assert match, "未能从 pyproject.toml 解析 dependencies"
    return re.findall(r'"([^"]+)"', match.group(1))


def package_names() -> set[str]:
    return {re.split(r"[<>=!~\[]", item, maxsplit=1)[0].strip() for item in declared_dependencies()}


def test_dependency_parser_reads_the_whole_array() -> None:
    """数组里最后一个依赖必须能被解析到——否则说明注释里的方括号截断了列表。"""
    deps = declared_dependencies()

    # 末位依赖是刻意选的：它紧跟依赖数组的收尾，一旦正则提前截断就会丢掉它。
    assert deps[-1].startswith("uvicorn"), f"依赖列表被截断，最后一项是 {deps[-1]!r}"
    assert len(deps) >= 15, f"只解析出 {len(deps)} 个直接依赖，疑似截断：{deps}"


@pytest.mark.parametrize("module", ["transformers", "onnxruntime", "numpy"])
def test_runtime_import_is_declared_as_direct_dependency(module: str) -> None:
    """延迟 import 的运行时依赖也必须显式声明，不能靠别的包传递带进来。"""
    assert module in package_names(), (
        f"{module} 被 app 代码 import，但不在 pyproject 的直接依赖里。"
        "打包脚本只装直接依赖，安装版会在运行期 ModuleNotFoundError。"
    )


def test_build_probe_covers_lazy_imports() -> None:
    """构建期导入自检必须覆盖 transformers/numpy 这类延迟 import。"""
    script = (
        PYPROJECT.parent / "scripts" / "build-backend-runtime.sh"
    ).read_text(encoding="utf-8")

    # 探针分两处：基础串 + 追加串。任一处漏掉都会让构建期探针失效。
    probe_lines = [
        line for line in script.splitlines() if "probe_imports" in line and "import" in line
    ]
    assert probe_lines, "未找到构建期 probe_imports"
    probe = "\n".join(probe_lines)
    for module in ("transformers", "onnxruntime", "numpy"):
        assert module in probe, (
            f"构建期探针未覆盖 {module}：源码里是延迟 import，"
            "漏掉就会拖到用户点「加载模型」才暴露"
        )

# ── check_runtime_imports.py：AST 级覆盖校验 ────────────────────────────────
# 这个脚本是 transformers 漏依赖事故的长期防线：延迟 import 逃过源码审查，
# 只有 AST 扫描能穿透函数体。测试直接跑它的 main()，验证判定逻辑而不只是跑通。

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_runtime_imports.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_runtime_imports", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_passes_on_current_source() -> None:
    assert _load_checker().main() == 0, "当前源码存在未被依赖声明覆盖的运行时 import"


def test_checker_finds_lazy_imports_inside_functions() -> None:
    """核心能力：必须能穿透函数体发现延迟 import。

    transformers 的import 就在 `ONNXEmbeddingModel.__init__` 里，
    只扫顶层节点的话这个脚本就毫无价值。
    """
    checker = _load_checker()
    imported = checker.imported_modules()

    assert "transformers" in imported, "没扫到 onnx_embedding 的延迟 import transformers"
    assert any("onnx_embedding" in where for where in imported["transformers"])
    # numpy 同理，也是延迟 import
    assert "numpy" in imported
    assert any("onnx_embedding" in where for where in imported["numpy"])


def test_checker_flags_undeclared_module() -> None:
    """把 transformers 从声明里去掉，校验必须失败并点名它+ 给出位置。"""
    pyproject = PYPROJECT
    original = pyproject.read_text(encoding="utf-8")
    stripped = original.replace('  "transformers>=5,<6",\n', "")
    assert stripped != original, "pyproject 里找不到 transformers 声明行"
    pyproject.write_text(stripped, encoding="utf-8")
    try:
        assert _load_checker().main() == 1, "漏声明依赖时校验应当失败"
    finally:
        pyproject.write_text(original, encoding="utf-8")

    assert _load_checker().main() == 0, "恢复声明后应当通过"


def test_checker_maps_import_name_to_distribution_name() -> None:
    """import 名 != PyPI 名时必须按分布名比对，否则误报一片。"""
    checker = _load_checker()
    declared = checker.declared_distributions()

    assert "beautifulsoup4" in declared and "bs4" not in declared
    assert checker.IMPORT_TO_DISTRIBUTION["bs4"] in declared
    assert checker.IMPORT_TO_DISTRIBUTION["sqlite_vec"] in declared
    assert checker.IMPORT_TO_DISTRIBUTION["rank_bm25"] in declared


def test_checker_excludes_optional_fp32_path() -> None:
    """sentence_transformers 只在 fp32 extra 用，不该被要求进默认依赖。"""
    checker = _load_checker()
    assert "sentence_transformers" in checker.OPTIONAL_ONLY
    assert checker.OPTIONAL_ONLY["sentence_transformers"] not in checker.declared_distributions()


def test_checker_is_wired_into_the_build_script() -> None:
    """校验必须接在构建流程里，否则只是个没人跑的工具。"""
    build_script = (PYPROJECT.parent / "scripts" / "build-backend-runtime.sh").read_text(
        encoding="utf-8"
    )

    assert "check_runtime_imports.py" in build_script, "构建脚本未调用 import 校验"
    check_line = next(
        line for line in build_script.splitlines() if "check_runtime_imports.py" in line
    )
    assert "if !" in check_line, "校验失败必须硬失败（if ! ... exit 1），不能只打印"


def test_checker_runs_as_standalone_script() -> None:
    """确认它真能在纯标准库解释器下独立运行（构建时就只有 interp 可用）。"""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, text=True, cwd=str(PYPROJECT.parent)
    )

    assert result.returncode == 0, f"独立运行失败:\n{result.stdout}\n{result.stderr}"
    assert "校验通过" in result.stdout
