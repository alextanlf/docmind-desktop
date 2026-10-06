#!/usr/bin/env python3
"""校验 app/ 的运行时 import 都被 [project].dependencies 覆盖。

为什么需要这个脚本：延迟 import（函数体内的 `import`）逃过了源码审查，而打包
`build-backend-runtime.sh` 只解析 [project].dependencies 并跳过 optional-dependencies。
一旦某个延迟 import 的包没被显式声明，**开发态 venv 里因为别的包传递带进来而正常，
安装版 runtime 里则完全没有** —— 表现为源码 imports 干净、pytest 全过、构建全绿，
只有用户点「加载模型」才炸出 ModuleNotFoundError。

transformers 就是这么漏的：它只是 fp32 extra 里 sentence-transformers 的传递依赖。

原理：用 ast 遍历**包括函数体内部**的所有 import（ast.walk 穿透延迟 import），
减去标准库和 app 自身，剩下的第三方顶层模块必须能在依赖声明里找到对应分发名。

退出码 0 = 全部覆盖；1 = 有未声明的依赖（构建期应硬失败）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
PYPROJECT = BACKEND_DIR / "pyproject.toml"

# import 名 != PyPI 分发名 的映射。左边是源码里写的模块名，右边是依赖声明里的包名。
IMPORT_TO_DISTRIBUTION = {
    "bs4": "beautifulsoup4",
    "markdownify": "markdownify",
    "pymupdf": "pymupdf",
    "rank_bm25": "rank-bm25",
    "sqlite_vec": "sqlite-vec",
    "pydantic": "pydantic-settings",
    "pydantic_settings": "pydantic-settings",
}

# 由某个已声明依赖**必然**带入的传递依赖，不要求单独声明。
# 键是 import 名，值是「谁带进来的」。这些若上游依赖树变动就会失效，
# 所以补进 probe_imports 的运行时探针里做二次确认。
PROVIDED_BY = {
    "starlette": "fastapi",
}

# 只在 fp32 回退路径使用的可选依赖。打包态必然内置 int8 ONNX 模型，
# 不装它（torch 约 500M），所以不在默认依赖里。
OPTIONAL_ONLY = {"sentence_transformers": "sentence-transformers"}


def _distribution_name(requirement: str) -> str:
    """`transformers>=5,<6` -> `transformers`"""
    return re.split(r"[<>=!~\[; ]", requirement.strip(), maxsplit=1)[0].strip()


def declared_distributions() -> set[str]:
    """解析 [project].dependencies。

    刻意复用 build-backend-runtime.sh 的同款非贪婪正则：两边必须对同一段文本
    得出一致结论，否则「校验通过但打包漏掉」这种矛盾会再次出现。
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    if not match:
        raise SystemExit("未能从 pyproject.toml 解析 [project].dependencies")
    return {_distribution_name(item) for item in re.findall(r'"([^"]+)"', match.group(1))}


def imported_modules() -> dict[str, list[str]]:
    """扫描 app/ 下所有 import，返回 {模块名: [出现位置, ...]}。

    用 ast.walk 而非只看顶层节点：延迟 import 在函数体里，是这类漏依赖的主要来源。
    """
    found: dict[str, list[str]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        try:
            relative = path.relative_to(BACKEND_DIR).as_posix()
        except ValueError:
            relative = path.name
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                found.setdefault(name, []).append(f"{relative}:{node.lineno}")
    return found


def main() -> int:
    stdlib = set(sys.stdlib_module_names)
    declared = declared_distributions()
    imported = imported_modules()

    undeclared: list[tuple[str, list[str]]] = []
    for module, locations in sorted(imported.items()):
        if module in stdlib or module == "app":
            continue
        if module in OPTIONAL_ONLY:
            continue
        if module in PROVIDED_BY:
            continue
        # 无映射时分布名就是模块名本身（多数情况）；有映射则查映射表。
        distribution = IMPORT_TO_DISTRIBUTION.get(module, module)
        if distribution in declared:
            continue
        undeclared.append((module, locations))

    if not undeclared:
        print(
            f"运行时 import 校验通过：{len(imported)} 个模块全部有依赖声明覆盖"
            f"（声明 {len(declared)} 个直接依赖）"
        )
        return 0

    print("运行时 import 未被 [project].dependencies 覆盖：", file=sys.stderr)
    for module, locations in undeclared:
        where = ", ".join(locations[:4])
        extra = f" 等 {len(locations)} 处" if len(locations) > 4 else ""
        print(f"  - {module}  ({where}{extra})", file=sys.stderr)
    print(
        "\n打包脚本只安装 [project].dependencies，跳过 optional-dependencies，"
        "所以开发态正常、安装版会 ModuleNotFoundError。\n"
        "修法：在 pyproject.toml 的 dependencies 里显式声明该包。\n"
        "注意 dependencies 段的注释里不要出现右方括号——打包脚本用非贪婪正则"
        "截取依赖列表，注释里的方括号会让它提前截断并静默漏项。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
