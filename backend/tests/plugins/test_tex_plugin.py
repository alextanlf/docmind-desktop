"""The TeX plugin: the first real format plugin.

Two different things are being checked here.

**That the plugin layer's promise holds.** A directory in the plugin directory
becomes an importable format, with a card and a file-picker entry, and no change
to the application. The real plugin is loaded through the real loader for that —
a fixture standing in for the plugin would only prove the fixture works.

**That the conversion is any good.** The output *is* the feature: a conversion
that loses a table or mangles a formula produces an index that retrieves the
wrong thing, and no amount of plumbing tests would notice. The constructs covered
below are not a wish list — they were counted in a corpus of real LaTeX reports
(section/subsection, booktabs tables with merged cells, lists, lstlisting,
tikzpicture figures, align*, and a great deal of inline and display math), and
every one of them appears in the sample document at the bottom of this file.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
import tomllib
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.document.builtin_formats import builtin_registry
from app.document.parser import DocumentParser
from app.plugins.catalog import PluginCatalog
from app.plugins.contributions import PluginHost
from app.plugins.loader import load_plugins
from app.remote.registry import ProviderRegistry
from app.schemas.imports import DownloadedDocument
from tests.plugins.conftest import PLUGINS_SOURCE

TEX_PLUGIN = PLUGINS_SOURCE / "docmind-tex"
MEDIA_TYPE = "text/x-tex"

#: A document exercising every construct the reference corpus uses, in the shape
#: the corpus uses it. Kept inline rather than as a fixture file so that what is
#: asserted and what is fed in sit next to each other.
SAMPLE = r"""
% 头部注释：应当被去掉
\documentclass[10pt,a4paper]{article}
\usepackage{ctex}
\usepackage{booktabs}
\title{编译器实验报告}
\author{张三}
\date{\today}

\begin{document}

\maketitle

\section{引言}
本文讨论 $\mathcal{L}$ 的性能，见表~\ref{tab:main}。准确率 100\% 。

\section{方法}
\subsection{符号表}
采用散列表实现，冲突用挂链法解决。

\begin{table}[H]
\centering
\begin{tabular}{lcc}
\toprule
\multirow{2}{*}{\textbf{真实}} & \multicolumn{2}{c}{\textbf{预测}} \\
\cmidrule(lr){2-3}
 & \textbf{正} & \textbf{负} \\
\midrule
\textbf{正} & 9 & 1 \\
\textbf{负} & 2 & 8 \\
\bottomrule
\end{tabular}
\caption{混淆矩阵}
\label{tab:main}
\end{table}

\begin{itemize}
  \item 第一点
  \item 第二点
    \begin{enumerate}
      \item 嵌套一
      \item 嵌套二
    \end{enumerate}
\end{itemize}

\begin{lstlisting}[language=Python]
def f(x):
    return x + 1
\end{lstlisting}

\begin{figure}[H]
\centering
\begin{tikzpicture}
\draw (0,0) -- (1,1);
\end{tikzpicture}
\caption{示意图}
\end{figure}

\begin{align*}
P &= \frac{a}{b} \\
R &= \frac{c}{d}
\end{align*}

\[
F1 = \frac{2PR}{P+R}
\]

\end{document}
"""


@pytest.fixture
def converter():
    """The conversion module, imported on its own.

    Deliberately not through the plugin contribution: the converter decides
    retrieval quality, and routing its tests through the contribution would make
    a conversion failure look like a plumbing failure.
    """
    sys.path.insert(0, str(TEX_PLUGIN))
    return importlib.import_module("docmind_tex.latex")


@pytest.fixture
def installed(tmp_path, store):
    """The plugin loaded the way a user loads it: a directory, symlinked in.

    A symlink rather than a copy because that is what a developer iterating on
    the plugin does, and because it means this test exercises the shipped files
    rather than a snapshot of them.
    """
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "docmind-tex").symlink_to(TEX_PLUGIN, target_is_directory=True)
    host = PluginHost(
        providers=ProviderRegistry(store),
        formats=builtin_registry(),
        credentials=store,
    )
    return host, load_plugins(host, plugins_dir)


# -- the plugin layer's promise ----------------------------------------------


def test_the_plugin_installs_by_being_a_directory(installed) -> None:
    host, diagnostics = installed

    assert diagnostics.failed == []
    assert [item["name"] for item in diagnostics.loaded] == ["docmind-tex"]
    assert host.formats.for_extension(".tex").name == "tex"
    # And the user-visible half: a card, and a format the file picker offers.
    assert [m.id for m in PluginCatalog(host).manifests()] == ["tex:core"]
    assert ".tex" in host.formats.pickable_extensions()


def test_the_card_describes_what_the_plugin_declares(installed) -> None:
    host, _ = installed

    card = PluginCatalog(host).manifests()[0]

    assert card.kind == "document_format"
    assert card.label == "TeX 文档"
    assert card.tag == "文档格式"
    # The suffixes are the grid's own data, and they are what makes the plugin
    # findable by someone who knows the extension they have.
    assert card.extensions == (".tex", ".latex")
    assert PluginCatalog(host).search("latex")


def test_a_tex_file_parses_through_the_registered_format(installed) -> None:
    host, _ = installed

    parsed = DocumentParser(host.formats).parse(
        DownloadedDocument(
            title="<uuid>.tex",
            source_url="staged://abc",
            media_type=MEDIA_TYPE,
            raw_bytes=SAMPLE.encode("utf-8"),
        )
    )

    # The staged file name is a bare UUID, so the document's own title matters:
    # without it, nothing about this document is findable by name.
    assert parsed.title == "编译器实验报告"
    # The heading chain, not just each heading: this is what a citation is keyed
    # on, and it is the reason the levels are mapped per command rather than
    # renumbered per document.
    assert [section.heading_path for section in parsed.sections] == [
        ["引言"],
        ["引言", "方法"],
        ["引言", "方法", "符号表"],
    ]


def test_the_plugin_declares_no_dependencies() -> None:
    """The property the whole plugin-directory mechanism rests on.

    The packaged runtime ships no installer, so a plugin that needed a package
    could not work on a machine that did not already have it. If this ever gains
    a dependency, "install a plugin by cloning it" stops being true, and the
    guide's promise breaks silently.
    """
    manifest = tomllib.loads((TEX_PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))

    assert manifest["project"]["dependencies"] == []


def test_the_converter_does_not_depend_on_docmind(converter) -> None:
    """Readable and testable on its own, which is a claim the module makes.

    Checked by walking the syntax tree rather than by searching the text: a
    comment mentioning ``app.document`` is not a dependency, and a lazily
    imported one inside a function is.
    """
    tree = ast.parse(Path(converter.__file__).read_text(encoding="utf-8"))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert [name for name in imported if name == "app" or name.startswith("app.")] == []
    assert imported, "没有任何导入说明这个断言其实没在检查东西"


def test_a_payload_that_is_not_text_is_rejected(installed) -> None:
    host, _ = installed
    format = host.formats.for_extension(".tex")
    payload = b"\xff\xfe\x00 not utf-8"

    # Rejected at staging, before a document full of replacement characters can
    # reach the index.
    assert format.validate is not None
    assert "UTF-8" in (format.validate(payload) or "")

    with pytest.raises(DomainError) as error:
        format.parse(
            DownloadedDocument(
                title="x.tex", source_url="staged://x", media_type=MEDIA_TYPE, raw_bytes=payload
            )
        )
    assert error.value.code == "SOURCE_UNSUPPORTED"


# -- headings, comments, metadata -------------------------------------------


def test_headings_map_to_a_stable_level_per_command(converter) -> None:
    markdown = converter.convert(
        r"\section{一}\subsection{二}\subsubsection{三}\paragraph{四}"
    ).markdown

    # Levels are not renumbered per document: a section is level three in every
    # file, which is what makes two documents' section paths comparable.
    assert [line for line in markdown.splitlines() if line] == [
        "### 一",
        "#### 二",
        "##### 三",
        "###### 四",
    ]


def test_a_starred_heading_is_still_a_heading(converter) -> None:
    markdown = converter.convert(r"\section*{一. 性能度量} 正文").markdown

    assert markdown.splitlines()[0] == "### 一. 性能度量"


def test_comments_go_and_escaped_percents_stay(converter) -> None:
    markdown = converter.convert(
        "% 整行注释\n正文 100\\% 保留\n正文 尾部 % 从这里开始注释\n下一行"
    ).markdown

    assert "整行注释" not in markdown
    assert "从这里开始注释" not in markdown
    assert "100% 保留" in markdown
    assert "下一行" in markdown


def test_the_preamble_goes_but_its_title_stays(converter) -> None:
    conversion = converter.convert(
        "\\documentclass{article}\n\\usepackage{ctex}\n"
        "\\title{真正的标题}\n\\begin{document}\n正文\n\\end{document}"
    )

    assert conversion.title == "真正的标题"
    assert "usepackage" not in conversion.markdown
    assert conversion.markdown.strip() == "正文"


def test_a_title_falls_back_to_the_first_heading(converter) -> None:
    conversion = converter.convert(r"\section{一. 性能度量} 正文")

    # A .tex assignment with no \title still names itself, and its opening
    # section beats the staged file's UUID.
    assert conversion.title == "一. 性能度量"


def test_a_line_break_is_not_mistaken_for_display_math(converter) -> None:
    """``\\\\[0.8em]`` is a break with extra space, not an opening ``\\[``.

    Read the wrong way, everything after it is swallowed into a formula — which
    is exactly what happened the first time, on a real report.
    """
    markdown = converter.convert(r"第一行\\[0.8em]第二行\\[0.35em]第三行").markdown

    assert "$$" not in markdown
    assert "0.8em" not in markdown
    assert "第二行" in markdown
    assert "第三行" in markdown


# -- tables ------------------------------------------------------------------


def test_a_booktabs_table_becomes_a_gfm_table(converter) -> None:
    markdown = converter.convert(SAMPLE).markdown
    # The table under 「方法」, found by its caption.
    table = markdown.split("**混淆矩阵**", 1)[1].strip().split("\n\n", 1)[0]
    rows = [row for row in table.splitlines() if row.strip()]

    assert rows[0] == "| **真实** | **预测** |  |"
    assert rows[1] == "| --- | --- | --- |"
    # The ``\\cmidrule`` is a rule, not a row, and the merged cells contribute
    # their text rather than their span.
    assert rows[2] == "|  | **正** | **负** |"
    assert rows[3] == "| **正** | 9 | 1 |"
    assert rows[4] == "| **负** | 2 | 8 |"
    # The column specification `{lcc}` is an argument of the environment, not a
    # cell, and must not become the header.
    assert "lcc" not in markdown


def test_a_caption_appears_once_and_before_its_table(converter) -> None:
    markdown = converter.convert(SAMPLE).markdown

    # Twice is the failure mode: the caption is emitted from the float and again
    # where it sat in the tabular.
    assert markdown.count("混淆矩阵") == 1
    assert markdown.index("**混淆矩阵**") < markdown.index("| **真实** |")


def test_a_figure_keeps_its_caption_and_drops_its_drawing(converter) -> None:
    markdown = converter.convert(SAMPLE).markdown

    assert "**示意图**" in markdown
    # A reference to a path that was never imported is noise; the drawing itself
    # cannot be carried over, so its source must not be either.
    assert "tikzpicture" not in markdown
    assert "\\draw" not in markdown


def test_a_cell_holding_a_line_break_does_not_end_its_row(converter) -> None:
    markdown = converter.convert(
        "\\begin{tabular}{ll}\n甲 & 很长的一行、\n换行继续 \\\\\n乙 & 短 \\\\\n\\end{tabular}"
    ).markdown

    rows = markdown.splitlines()
    # A newline inside a cell would split the row and break the table for every
    # row after it, so the first row is the one that would go wrong.
    assert rows[0] == "| 甲 | 很长的一行、 换行继续 |"
    assert rows[1] == "| --- | --- |"
    assert rows[2] == "| 乙 | 短 |"


# -- lists -------------------------------------------------------------------


def test_lists_nest_and_enumerate_counts(converter) -> None:
    markdown = converter.convert(SAMPLE).markdown

    assert markdown.count("- 第一点") == 1
    assert "  - 第二点" not in markdown
    assert "- 第二点" in markdown
    # Nested items are indented under the item they belong to.
    assert "  1. 嵌套一" in markdown
    assert "  2. 嵌套二" in markdown


def test_an_item_whose_body_holds_a_formula_keeps_its_marker(converter) -> None:
    markdown = converter.convert("\\begin{enumerate}\n\\item 由定义：\n\\[ x = 1 \\]\n\\end{enumerate}").markdown

    lines = markdown.splitlines()
    # The item's own prose keeps the marker; the formula follows as a block,
    # indented under it rather than flattened into the sentence.
    assert lines[0] == "1. 由定义："
    assert lines[1] == "  $$"
    assert "x = 1" in markdown


def test_an_item_that_starts_with_a_formula_gives_the_marker_its_own_line(converter) -> None:
    """The other half of the same rule, and the only case that exercises it.

    When the prose comes first the marker shares its line either way, so without
    this input the block-level check could be deleted with nothing going red.
    """
    markdown = converter.convert("\\begin{itemize}\n\\item\n\\[ x = 1 \\]\n\\end{itemize}").markdown

    lines = markdown.splitlines()
    # Markdown cannot put a formula mid-line, so the marker stands alone rather
    # than the formula being flattened into the item's text.
    assert lines[0] == "-"
    assert lines[1] == "  $$"
    assert "x = 1" in markdown


# -- code and math -----------------------------------------------------------


def test_a_listing_becomes_a_fenced_block_with_its_language(converter) -> None:
    markdown = converter.convert(SAMPLE).markdown

    assert "```python\ndef f(x):\n    return x + 1\n```" in markdown


def test_a_fence_inside_a_listing_widens_the_fence(converter) -> None:
    markdown = converter.convert(
        "\\begin{lstlisting}\n```\nnot a nested block\n```\n\\end{lstlisting}"
    ).markdown

    # Escaping would change the bytes, and a verbatim block's bytes are the point.
    assert "````" in markdown
    assert "```\nnot a nested block\n```" in markdown


def test_display_math_keeps_its_commands(converter) -> None:
    markdown = converter.convert(SAMPLE).markdown

    assert "$$\nP &= \\frac{a}{b} \\\\\nR &= \\frac{c}{d}\n$$" in markdown
    assert "$$\nF1 = \\frac{2PR}{P+R}\n$$" in markdown


def test_inline_math_survives_verbatim(converter) -> None:
    markdown = converter.convert(r"学习器 $\mathcal{L}$ 与 $x_1$ 的关系").markdown

    # A symbol table that guessed at \mathcal would lose more than it gained.
    assert r"$\mathcal{L}$" in markdown
    assert "$x_1$" in markdown


def test_a_reference_keeps_its_key(converter) -> None:
    markdown = converter.convert(r"见表~\ref{tab:main} 与 \cite{knuth1984}").markdown

    # Resolving the number would mean inventing the caption; dropping the key
    # would lose the link entirely.
    assert "[tab:main]" in markdown
    assert "[knuth1984]" in markdown
    # ``~`` is a non-breaking space, not a character to keep.
    assert "~" not in markdown


# -- text hygiene ------------------------------------------------------------


def test_grouping_braces_and_font_switches_leave_no_trace(converter) -> None:
    markdown = converter.convert(
        "{\\LARGE \\textbf{大标题}}\\par\\noindent{\\small 小字}"
    ).markdown

    assert markdown == "**大标题**\n\n小字"


def test_a_paragraph_starting_with_a_hash_does_not_invent_a_section(converter) -> None:
    markdown = converter.convert("\\section{真章节}\n\n# 这不是标题\n\n正文").markdown

    # Section splitting keys on a line-leading ``#``, so an unescaped one would
    # silently add a section that the document does not have.
    assert markdown.count("### 真章节") == 1
    assert "\\# 这不是标题" in markdown
    from app.document.markdown_sections import sections_from_markdown

    # One section, not two: the escaped hash stayed inside the section it was
    # written in instead of opening a new one.
    assert [section.heading_path for section in sections_from_markdown(markdown)] == [
        ["真章节"]
    ]


def test_fill_in_the_blank_rules_do_not_leave_their_arguments(converter) -> None:
    markdown = converter.convert(
        r"姓名：\rule[-0.6ex]{1.9cm}{0.4pt}\quad 学号：\rule{2.1cm}{0.4pt}"
    ).markdown

    assert "rule" not in markdown
    assert "1.9cm" not in markdown
    assert "0.4pt" not in markdown


def test_the_sample_converts_without_leaving_latex_outside_math(converter) -> None:
    """The whole-document check: no stray commands, no stray braces.

    A single leftover ``\\foo`` is not itself a disaster, but it means the text
    around it was read wrong, and that is what puts the wrong thing in the index.
    """
    markdown = converter.convert(SAMPLE).markdown
    outside_math = _outside_math(markdown)

    assert "{" not in outside_math
    assert "}" not in outside_math
    assert not [token for token in ("\\begin", "\\end", "\\item", "\\textbf") if token in outside_math]


def _outside_math(markdown: str) -> str:
    """The markdown with every display block and inline span of math removed.

    Math is *supposed* to still contain braces and backslashes, so a check for
    leftover LaTeX has to look at everything else.
    """
    kept: list[str] = []
    in_display = False
    for line in markdown.splitlines():
        if line.strip() == "$$":
            in_display = not in_display
            continue
        if in_display:
            continue
        kept.append(re.sub(r"\$[^$]*\$", "", line))
    return "\n".join(kept)
