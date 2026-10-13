"""量出真实课件里「图片」的分布，再决定实现取舍。

**为什么先测量。** 图片通路有两组阈值和一条架构假设，它们都只能由分布决定：

* **矢量 vs 位图**：`get_images()` 看不到矢量图表（实测一张 matplotlib 折线图的
  XObject 数为 0），若只按位图做候选发现，最该进索引的图表反而一个都进不来。
  这个比例决定 `figures.py` 要不要维护两条独立的候选通路。
* **「需 OCR」的占比**：整页一张图且无文本层 = 扫描件。比例高说明 OCR 是主路径，
  比例低说明它只是补漏。
* **图与表的可对齐比例**：课件里的折线图通常配一张数据表格。若对齐率高，
  「从矢量路径反算数据」就不值得做 —— 抄表格远比反算可靠。这条是实测结论，
  不是设计偏好，所以必须先有数字。

**第一版测量是错的，错法值得记下来。** 初版把矢量路径按 bbox 相交并成团，跑出
559 页 / 559 个「矢量图形」—— 恰好每页一个。渲染三页真实内容后原因就清楚了（见
`docs/figure-distribution.json` 的 `probe_notes`）：

1. **背景矩形在每页凑一个数**。课件模板整块底色是一个 `draw_rect`，面积远超阈值，
   于是每页稳定贡献一个假图形。实测证据：05-Markov-Source p8 通篇文字与公式、没有
   任何插图，却被算成一个矢量图形。
2. **公式区也被算成图形**。Word/PPT 导出的公式由大量带边框的文本矩形组成，合并后
   同样超过面积阈值。
3. **矢量图形会包住位图**。reinforcement-learning p5 的 3×3 状态网格由「矢量边框 +
   单元格底色矩形 + 单元格文字」构成，是一个图形，但如果只合并矢量路径、再单独看待
   位图，同一个图形会被拆成两个候选。

因此本版改成：先剔除背景（贴边 或 面积过大 或 长条），再**把矢量与位图放进同一个
并查集**合并，最后要求团内含有真正的图形元素（位图，或非纯文字矩形的矢量路径）。

**为什么不用断言。** 这是一支测量脚本，不是测试：它的产出是 JSON 证据，供人读、
供后续决策引用。阈值一律不写进代码，避免「跑一个想当然的阈值」。

用法::

    uv run python scripts/measure_figure_distribution.py 样本/*.pdf --out result.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

#: 一个连通的图形团要多大面积占比才算「一个图形」，而不是线条/装饰。
#: 该值是**起点不是结论** —— 测量脚本存在的意义就是让它在数据下被推翻。
_VECTOR_MIN_AREA_RATIO = 0.02
#: 低于此像素数的位图不参与统计（图标、项目符号）。
_MIN_RASTER_PIXELS = 64 * 64
#: 整页图片的判定：bbox 面积占页面 ≥ 该比例。
_FULL_PAGE_RATIO = 0.8
#: 背景判定：面积占比 ≥ 此值，且（贴近页边 或 长宽比极端）→ 模板底色，不是图形。
_BACKGROUND_AREA_RATIO = 0.5
_BACKGROUND_EDGE_TOLERANCE = 6.0
_STRIP_ASPECT_RATIO = 10.0
#: 合并时的邻接容差，与 PDF 单位（点）一致。
_MERGE_PADDING = 2.0
#: 团里真正的图形元素占团面积的下限。低于此值的团只是「一堆文字矩形」。
_VISUAL_COVERAGE_RATIO = 0.25
#: 整页图判定：图片覆盖率不低于此值，就认为这一页「主体是图」。
_IMAGE_ONLY_MIN_COVERAGE = 0.4
#: 小于等于这么多字符的整页图 = 文字层等于没有（`编译原理` 的 21 字符水印）。
_IMAGE_ONLY_MIN_CHARS = 70
#: 整页图页面上，每平方点的最少字符数才算「文字层可用」。
#: 实测分界：`算法导论` 1108 字符 / (490×733) 点 ≈ 0.0031；`编译原理` 21 / 大页 ≈ 0.00006。
_SCANNED_TEXT_MIN_DENSITY = 0.0015

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def _clean(text: str) -> str:
    """丢掉孤立的代理字符。

    MuPDF 对数学字母数字符号（U+1D400 段，公式里常见）是按 UTF-16 半字吐出来的，
    截断后就成了孤立代理项。它既不能编码成 UTF-8，也会在下游 ``write_text`` 直接炸。
    """
    return text.encode("utf-8", "replace").decode("utf-8", "replace")


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN.finditer(text)}


@dataclass
class RasterRef:
    xref: int
    width: int
    height: int
    digest: str


@dataclass
class Figure:
    page: int
    area_ratio: float
    #: 团内矩形覆盖不到的空白占比。背景/版式元素会留下大块空洞。
    emptiness: float
    #: 图形元素（位图 + 非纯文字矩形路径）覆盖团面积的比例。
    visual_ratio: float
    text: str
    rasters: list[RasterRef] = field(default_factory=list)
    #: 团内被 `find_tables` 认出的表格，序列化成 markdown。这是「图里自带的数据表」。
    grid_markdown: str = ""

    @property
    def has_text(self) -> bool:
        return bool(_TOKEN.search(self.text))

    @property
    def is_full_page(self) -> bool:
        return self.area_ratio >= _FULL_PAGE_RATIO


@dataclass
class DocumentReport:
    path: str
    pages: int
    #: 实际测过的页数。未采样时等于 ``pages``；密度指标用它作分母。
    sampled_pages: int = 0
    pages_without_text: int = 0
    #: 「整页图」的页数：幻灯片被导出成一张整页位图，文字层只剩页码。
    #: 这类页必须**整页 OCR**，按 figure 逐个处理会把它当成一张无意义的大图。
    image_only_pages: int = 0
    figures: list[Figure] = field(default_factory=list)
    excluded_backgrounds: int = 0
    repeated_digests: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def rasters(self) -> list[RasterRef]:
        return [raster for figure in self.figures for raster in figure.rasters]

    @property
    def full_page_figures(self) -> list[Figure]:
        return [figure for figure in self.figures if figure.is_full_page]

    @property
    def figures_with_text(self) -> list[Figure]:
        return [figure for figure in self.figures if figure.has_text]

    @property
    def vector_figures(self) -> list[Figure]:
        return [figure for figure in self.figures if not figure.rasters]

    @property
    def figures_with_grid(self) -> list[Figure]:
        return [figure for figure in self.figures if figure.grid_markdown]

    @property
    def vector_with_text(self) -> list[Figure]:
        return [figure for figure in self.vector_figures if figure.has_text]


def _page_text_blocks(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    blocks: list[tuple[pymupdf.Rect, str]] = []
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
        if isinstance(text, str) and text.strip():
            blocks.append((pymupdf.Rect(x0, y0, x1, y1), text.strip()))
    return blocks


@dataclass
class _Piece:
    """一个待合并的元素：矢量路径或位图。"""

    rect: pymupdf.Rect
    is_visual: bool
    raster: RasterRef | None = None


def _is_background(rect: pymupdf.Rect, page: pymupdf.Page) -> bool:
    """模板底色而不是图形。

    课件的整块背景是一个 `draw_rect`，面积接近整页，因此靠面积阈值拦不住它 ——
    实测它会在每页稳定贡献一个假图形（05-Markov-Source p8 通篇无插图却中了）。
    三个判别条件都只针对「版式元素」这个形态，不涉及内容语义：
    贴页边、面积过大、长条（页眉/页脚/分隔线）。
    """
    page_area = abs(page.rect.get_area())
    if not page_area:
        return False
    if rect.get_area() / page_area < _BACKGROUND_AREA_RATIO:
        # 面积不大，但形状是贯穿整页的长条，同样是版式元素。
        width, height = rect.width, rect.height
        if min(width, height) <= 0:
            return False
        return max(width, height) / min(width, height) >= _STRIP_ASPECT_RATIO
    tolerance = _BACKGROUND_EDGE_TOLERANCE
    touches_edge = (
        rect.x0 <= page.rect.x0 + tolerance
        or rect.y0 <= page.rect.y0 + tolerance
        or rect.x1 >= page.rect.x1 - tolerance
        or rect.y1 >= page.rect.y1 - tolerance
    )
    width, height = rect.width, rect.height
    strip = (
        min(width, height) > 0 and max(width, height) / min(width, height) >= _STRIP_ASPECT_RATIO
    )
    return touches_edge or strip


def _visual_drawing(drawing: dict) -> bool:
    """判断一条矢量路径是不是「图形」而不是「文字框」。

    Word/PPT 导出的公式由大量带边框的文本矩形组成，它们合并后同样超过面积阈值。
    判据只看路径的**原语种类**：全是矩形（``re``）就是文本/版式容器；一旦出现曲线、
    折线、圆、四边形，就说明画的是图形。

    **不要在这里看 ``fill``。** 初版把「有填充色」也算作图形，于是公式区里每个带底色
    的文本矩形都成了图形元素，整块公式区被误判为一张插图。
    """
    kinds = {item[0] for item in (drawing.get("items") or []) if item}
    return bool(kinds - {"re"})


def _merge_pieces(pieces: list[_Piece]) -> list[tuple[pymupdf.Rect, list[_Piece]]]:
    """把互相邻接的元素并成团。

    位图和矢量**放进同一个并查集**：reinforcement-learning p5 的 3×3 状态网格是
    「矢量边框 + 单元格底色矩形 + 单元格文字」，若两条通路各自成团，同一个图形会
    被拆成两个候选。

    **配对用网格分桶，不是双重循环。** 双重循环在课件上够快（每页几十条路径），
    在教材页上直接卡死：一页几百条路径就是几万次比较，乘以几千页跑不完。按「最大元素
    尺寸」分桶后只需检查自身与相邻的 9 个桶 —— 相交的两元素必然落在同一或相邻桶里，
    所以结果与双重循环一致，复杂度却接近线性。
    """
    parents = list(range(len(pieces)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[left_root] = right_root

    if pieces:
        cell = max(
            max(piece.rect.width, piece.rect.height) for piece in pieces
        )
        cell = max(cell, 1.0)
        buckets: dict[tuple[int, int], list[int]] = {}
        for index, piece in enumerate(pieces):
            key = (int(piece.rect.x0 // cell), int(piece.rect.y0 // cell))
            buckets.setdefault(key, []).append(index)
        for (column, row), members in buckets.items():
            neighbours: list[int] = []
            for delta_column in (-1, 0, 1):
                for delta_row in (-1, 0, 1):
                    neighbours.extend(buckets.get((column + delta_column, row + delta_row), []))
            for index in members:
                for other in neighbours:
                    if other > index and pieces[index].rect.intersects(pieces[other].rect):
                        union(index, other)

    grouped: dict[int, list[_Piece]] = {}
    for index, piece in enumerate(pieces):
        grouped.setdefault(find(index), []).append(piece)
    merged = []
    for members in grouped.values():
        union_rect = members[0].rect
        for piece in members[1:]:
            union_rect = union_rect | piece.rect
        merged.append((union_rect, members))
    return merged


def _grid_markdown(page: pymupdf.Page, rect: pymupdf.Rect) -> str:
    """把图形内部的表格提取成 markdown。

    **实测结论是「不要做」。** 初版把它当成「折线图配数据表」的落点，结果：

    * 在这批课件上，`find_tables` 对真正的状态网格（reinforcement-learning p5 的 3×3
      格子）返回 **0** —— 它不认带虚线的示意图。
    * 而它会**编造**表格：05-Markov-Source p24 上一段公式被切成 28×29pt 的 1 行 2 列，
      内容是 "符号\nsymbo" / "l"，纯属噪声。
    * 全量命中率 0.6%（3/506），且这 3 个还要人工核对才敢用。

    所以生产代码里没有这一层。脚本保留它只是为了**让这个结论可复现**：删掉它就没人
    能重新验证「图内表格提取在这个语料上不成立」。
    """
    try:
        found = page.find_tables(clip=rect)
    except Exception:  # noqa: BLE001 - 畸形页会让 MuPDF 直接抛错
        return ""
    lines: list[str] = []
    for table in found.tables:
        for row in table.extract():
            cells = [_clean(str(cell)).replace("|", "\\|") for cell in row]
            if any(cell.strip() for cell in cells):
                lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _detect_figures(
    page: pymupdf.Page, page_number: int, *, want_grids: bool = False
) -> tuple[list[Figure], int]:
    page_area = abs(page.rect.get_area())
    if not page_area:
        return [], 0

    # 文字层不可用的页上，正文和插图都在同一张整页图里，**没有可独立提取的图**。
    # 实测：CS Theory Toolkit 的 130 个「文字层不可用」页曾经产出 68 个假图，
    # 全是面积 0.53–0.65 的整页底图。这种页的处理方式是整页 OCR，不是找图。
    if _is_image_only_page(page):
        return [], 0

    pieces: list[_Piece] = []
    excluded = 0
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not isinstance(rect, pymupdf.Rect) or rect.is_empty:
            continue
        if _is_background(rect, page):
            excluded += 1
            continue
        pieces.append(_Piece(rect=rect + (-_MERGE_PADDING, -_MERGE_PADDING, _MERGE_PADDING, _MERGE_PADDING),
                             is_visual=_visual_drawing(drawing)))

    for info in page.get_image_info(xrefs=True, hashes=True):
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if width * height < _MIN_RASTER_PIXELS:
            continue
        rect = pymupdf.Rect(info["bbox"])
        if _is_background(rect, page):
            excluded += 1
            continue
        # 「扫描 + OCR」的教材每页都是一张整页底图，文字层却是真的（实测
        # 算法导论 1100–1700 字符、Introduction to Linear Algebra 约 1900 字符，
        # 内容都与书页一致）。这种整页底图不是插图，把它当 figure 会在每页产出一个
        # 假候选，而且是 100% 命中的假候选 —— 实测算法导论 120 页里 0 条矢量路径，
        # 全靠这条规则它才没有被判成「120 张图」。
        # 判据必须看文字层质量：文字层没有的整页图是纯扫描件，那才是真正需要整页 OCR
        # 的对象，不能在这里丢掉。
        page_area = abs(page.rect.get_area())
        if (
            page_area
            and rect.get_area() / page_area >= _FULL_PAGE_RATIO
            and _text_layer_quality(page) == "good"
        ):
            excluded += 1
            continue
        raster = RasterRef(
            xref=int(info.get("xref") or 0),
            width=width,
            height=height,
            digest=(info.get("digest") or b"").hex()[:16],
        )
        pieces.append(
            _Piece(
                rect=rect + (-_MERGE_PADDING, -_MERGE_PADDING, _MERGE_PADDING, _MERGE_PADDING),
                is_visual=True,
                raster=raster,
            )
        )

    figures: list[Figure] = []
    for union, members in _merge_pieces(pieces):
        ratio = union.get_area() / page_area
        if ratio < _VECTOR_MIN_AREA_RATIO:
            continue
        covered = sum(piece.rect.get_area() for piece in members)
        emptiness = max(0.0, 1.0 - covered / union.get_area()) if union.get_area() else 1.0
        visual = sum(piece.rect.get_area() for piece in members if piece.is_visual)
        visual_ratio = visual / union.get_area() if union.get_area() else 0.0
        # 既没有位图、又只是「一堆文字矩形」的团不是图（公式区、目录区）。
        if visual_ratio < _VISUAL_COVERAGE_RATIO:
            continue
        figures.append(
            Figure(
                page=page_number,
                area_ratio=round(ratio, 4),
                emptiness=round(emptiness, 4),
                visual_ratio=round(visual_ratio, 4),
                text=_clean(page.get_text("text", clip=union)).strip(),
                rasters=[piece.raster for piece in members if piece.raster is not None],
                # 默认不跑：find_tables 又慢又不可靠（见 _grid_markdown 的实测结论）。
                grid_markdown=_grid_markdown(page, union) if want_grids else "",
            )
        )
    return figures, excluded


def _text_layer_quality(page: pymupdf.Page) -> str:
    """这一页的文字层属于哪种：``good`` / ``sparse`` / ``none``。

    教材把这件事变得比课件复杂得多，而且**只数字符会判错**。实测同一批教材里：

    * `算法导论`（扫描+OCR）：每页一张 4080×6110 整页图，OCR 文字层 1100–1700 字符，
      内容与书页一致（"第24 章单源最短路径…"）→ 文字层可用，整页图不是插图。
    * `Introduction to Linear Algebra`：同样是扫描+OCR，1800 字符、完全可用。
    * `统计学习方法` / `数据结构与算法分析`：整页图 + **0 字符**。
    * `编译原理`：整页图 + 21 字符，内容是 "http://www.pin5i.com/"（水印），文字层等于没有。

    后两类必须整页 OCR；前两类**不能** OCR —— 已经是对的，OCR 只会把好文字换成差文字。
    所以判据是「文字密度」而不是字符数绝对值：`编译原理` 的 21 字符在大页面上等于零，
    而 `算法导论` 的 1108 字符是真实正文。
    """
    page_area = abs(page.rect.get_area())
    covered = 0.0
    if page_area:
        covered = (
            sum(
                pymupdf.Rect(info["bbox"]).get_area()
                for info in page.get_image_info(xrefs=True)
            )
            / page_area
        )
    scans = covered >= _IMAGE_ONLY_MIN_COVERAGE
    text = page.get_text("text").strip()
    if not scans:
        return "good" if text else "none"
    if len(text) <= _IMAGE_ONLY_MIN_CHARS:
        return "none"
    # 密度门槛：整页扫描件上，一页真正的正文远超这个量。
    if len(text) / page_area < _SCANNED_TEXT_MIN_DENSITY:
        return "sparse"
    return "good"


def _is_image_only_page(page: pymupdf.Page) -> bool:
    """这一页的主体内容只存在于图片里，文字层给不出东西。

    初版只判「页面文字为空」，于是 ``pages_without_text`` 报 0 —— 一个假绿：页码那
    1 个字符足以让检测通过，而实际上 reinforcement-learning.pdf 与
    Semantic Communications.pdf 整份都是幻灯片截图，正文一个字都抽不出来。
    """
    return _text_layer_quality(page) in {"none", "sparse"}


def measure_pdf(
    path: Path, max_pages: int | None = None, *, want_grids: bool = False
) -> DocumentReport:
    """测量一份 PDF。

    ``max_pages`` 给长篇教材用：6000 页 × O(n²) 聚簇跑不完，而阈值该由**形态分布**
    决定、不必由每一页决定。采样按等间隔取，避免只看到某一章。密度指标一律用
    ``sampled_pages`` 作分母，不能用总页数 —— 否则采样会系统性低估。
    """
    report = DocumentReport(path=path.name, pages=0)
    try:
        document = pymupdf.open(path)
    except Exception as error:  # noqa: BLE001 - 测量脚本要把失败也记下来，不能静默跳过
        report.error = f"{type(error).__name__}: {error}"
        return report

    try:
        total = document.page_count
        report.pages = total
        if max_pages is not None and total > max_pages:
            step = total / max_pages
            indices = [int(index * step) for index in range(max_pages)]
        else:
            indices = list(range(total))
        report.sampled_pages = len(indices)
        for index in indices:
            page = document[index]
            if not page.get_text("text").strip():
                report.pages_without_text += 1
            if _is_image_only_page(page):
                report.image_only_pages += 1
            figures, excluded = _detect_figures(page, index + 1, want_grids=want_grids)
            report.figures.extend(figures)
            report.excluded_backgrounds += excluded
    finally:
        document.close()

    counts: dict[str, int] = {}
    for raster in report.rasters:
        counts[raster.digest] = counts.get(raster.digest, 0) + 1
    report.repeated_digests = {digest: n for digest, n in counts.items() if n >= 3}
    return report


def _summary(reports: list[DocumentReport]) -> dict[str, object]:
    pages = sum(report.pages for report in reports)
    sampled = sum(report.sampled_pages or report.pages for report in reports)
    figures = [figure for report in reports for figure in report.figures]
    vector_figures = [figure for figure in figures if not figure.rasters]
    vector_text = [figure for figure in vector_figures if figure.has_text]
    full_page = [figure for figure in figures if figure.is_full_page]
    with_grid = [figure for figure in figures if figure.grid_markdown]
    without_text = [figure for figure in figures if not figure.has_text]
    no_text_pages = sum(report.pages_without_text for report in reports)
    image_only_pages = sum(report.image_only_pages for report in reports)
    documents_all_image_only = [
        report.path
        for report in reports
        if (report.sampled_pages or report.pages)
        and report.image_only_pages >= (report.sampled_pages or report.pages) * 0.8
    ]
    return {
        "documents": len(reports),
        "pages": pages,
        # 采样后所有**密度**指标的分母：用总页数会系统性低估。
        "sampled_pages": sampled,
        "pages_without_text": no_text_pages,
        "pages_without_text_ratio": round(no_text_pages / sampled, 4) if sampled else 0.0,
        # 「页码也算有文字」曾让上一个指标报 0，于是整份截图型课件的严重程度被藏住。
        "image_only_pages": image_only_pages,
        "image_only_pages_ratio": (
            round(image_only_pages / sampled, 4) if sampled else 0.0
        ),
        "documents_all_image_only": documents_all_image_only,
        "figures": len(figures),
        "figures_per_page": round(len(figures) / sampled, 3) if sampled else 0.0,
        "full_page_figures": len(full_page),
        "raster_instances": sum(len(figure.rasters) for figure in figures),
        "vector_figures": len(vector_figures),
        "vector_figures_with_text": len(vector_text),
        # 零成本取语义的上界：矢量图里带文字的，clip 文本层就能直接拿到轴名/刻度/图注。
        "vector_with_text_ratio": (
            round(len(vector_text) / len(vector_figures), 4) if vector_figures else 0.0
        ),
        # 必须走 OCR/VLM 的下界：图内一个可检索词都没有。
        "figures_without_text": len(without_text),
        "figures_without_text_ratio": (
            round(len(without_text) / len(figures), 4) if figures else 0.0
        ),
        # 「折线图配数据表」成立的位置：表在图**里面**。
        "figures_with_internal_grid": len(with_grid),
        "figures_with_grid_ratio": (
            round(len(with_grid) / len(figures), 4) if figures else 0.0
        ),
        "excluded_backgrounds": sum(report.excluded_backgrounds for report in reports),
        "repeated_raster_digests": sum(len(report.repeated_digests) for report in reports),
        "repeated_raster_instances": sum(
            sum(report.repeated_digests.values()) for report in reports
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--sample-grids",
        type=int,
        default=0,
        help="打印前 N 个「图内含表格」的样本，用于人工核对提取是否真的对",
    )
    parser.add_argument(
        "--grids",
        action="store_true",
        help="同时跑 find_tables 提取图内表格。默认关闭：慢且实测不可靠，只为复现结论而保留",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="每份文档最多测多少页（等间隔采样）。长篇教材必须采样，否则 O(n²) 聚簇跑不完",
    )
    arguments = parser.parse_args()

    reports: list[DocumentReport] = []
    for path in arguments.paths:
        if path.suffix.lower() != ".pdf":
            print(f"跳过非 PDF：{path.name}", file=sys.stderr)
            continue
        reports.append(
            measure_pdf(path, max_pages=arguments.max_pages, want_grids=arguments.grids)
        )

    payload = {
        "summary": _summary(reports),
        "documents": [
            {
                **asdict(report),
                "figures": [asdict(figure) for figure in report.figures],
            }
            for report in reports
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
        print(f"已写入 {arguments.out}")
    else:
        print(text)

    if arguments.sample_grids:
        shown = 0
        for report in reports:
            for figure in report.figures:
                if not figure.grid_markdown:
                    continue
                print(f"\n--- {report.path} p{figure.page} 图内表格 ---")
                print(figure.text[:200])
                print(figure.grid_markdown[:400])
                shown += 1
                if shown >= arguments.sample_grids:
                    return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
