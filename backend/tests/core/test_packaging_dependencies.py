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