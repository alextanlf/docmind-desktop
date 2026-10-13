"""在一页里找出「真正的插图」，并给出一张图应有的上下文。

**为什么不能只调 `get_images()`。** 实测一张 matplotlib 折线图存成 PDF 后，
`get_images()` 返回 0 —— 曲线、坐标轴、刻度全是矢量路径，图里一个位图都没有。
只按位图找候选，最该进索引的图表一个都进不来。反过来，教材和课件里大量「图」是
**矢量图形包住位图**（reinforcement-learning 的 3×3 状态网格 = 矢量边框 + 单元格
底色 + 单元格文字），两条通路各自成团会把一张图拆成两个候选。所以这里把位图与矢量
路径放进**同一个并查集**。

**为什么必须剔背景。** 课件模板的整块底色是一个 `draw_rect`，面积接近整页。不剔它
的话每页至少产出一个假图 —— 实测 12 份课件剔除 2397 个、12 本教材剔除 4593 个模板
元素之后，才剩下 346 / 744 个真候选。这不是优化项，是可用性问题。

**为什么纯文字矩形不算图。** Word/PPT 导出的公式由大量带边框的文本矩形组成，合并后
同样超过面积阈值。判据只看路径**原语种类**：全是矩形（``re``）就是文字/版式容器。

**这一层不做的事。** 不提取图内表格、不从矢量路径反算数据。前者实测命中率 0.6%
（3/506）且 `find_tables` 会在数学排版上**编造**表格（把公式切成 28×29pt 的 1 行 2 列）；
后者在课件上覆盖率极低。测量脚本 `scripts/measure_figure_distribution.py` 保留了这两项
实验，结论与被推翻的过程都在那里，这里不重复它们的代码。
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import pymupdf

#: 一个图形团要多大面积占比才算「一张图」，而不是线条或装饰。
MIN_AREA_RATIO = 0.02
#: 低于此像素数的位图不参与（图标、项目符号）。
MIN_RASTER_PIXELS = 64 * 64
#: 面积占比达到此值即视为「整页图」。
FULL_PAGE_RATIO = 0.8
#: 背景判定：面积占比达到此值，且贴页边 → 模板底色。
BACKGROUND_AREA_RATIO = 0.5
#: 背景判定：宽或高占到页面的这个比例，且贴页边 → 整宽/整高色带。
BACKGROUND_SPAN_RATIO = 0.9
BACKGROUND_EDGE_TOLERANCE = 6.0
STRIP_ASPECT_RATIO = 10.0
#: 版式带判定：某一维跨越页面的比例下限。
BAND_SPAN_RATIO = 0.8
#: 版式带判定：另一维不得超过页面的这个比例。
BAND_THICKNESS_RATIO = 0.15
#: 至少这么多条等宽横带同时出现，才判定为版式骨架（单条宽图是内容）。
BAND_MIN_COUNT = 2
#: 页眉/页脚带占页高的比例。课件标题横幅贴在图区上沿，不贴页边。
HEADER_FOOTER_BAND_RATIO = 0.25
#: 页眉/页脚带内的扁平块判定：宽高比下限。
#: 不能只看宽高比：实测还有 34–37% 宽、7% 高、宽高比 5.3 的校徽/标题小图，
#: 宽高比门槛会把它们放过。
HEADER_FOOTER_ASPECT_RATIO = 5.0
#: 页眉/页脚带内的扁平块判定：宽度占页宽的下限。比这更窄的是行内小图标，
#: 另由 MIN_RASTER_PIXELS 与面积门槛处理。
HEADER_FOOTER_MIN_WIDTH_RATIO = 0.25
#: 页眉/页脚带内的扁平块判定：高度占页高的上限。超过它就不是横幅而是正文区块。
HEADER_FOOTER_MAX_HEIGHT_RATIO = 0.08
#: 取图内文字时向外扩的边距（点）。
#:
#: 必须外扩：MuPDF 的 ``clip`` 以**文字块**为单位判断纳入与否，块的起点落在范围外就
#: 整块丢掉。实测 'Voltage (mV)' 跨在边界上时只返回 'mV)'，真实图上的
#: 'having a order of two.' 只返回 'ng a order of two.'。不外扩就会系统性丢掉图两侧的
#: 轴标签。
FIGURE_TEXT_PADDING = 6.0
#: 搜索邻近文字块的半径（点）。实测 279 张真实图：84.6% 的最近文字块在 6 点内、
#: 90.0% 在 12 点内、92.8% 在 20 点内 —— 12 点之后收益递减。
CAPTION_SEARCH_PADDING = 12.0
#: 并块时纵向允许外扩的总量（点），硬上限。
#:
#: MuPDF 按**词的起点**决定是否纳入 clip：起点在范围外就整块丢掉，实测要到外扩 40 点
#: 才吐出被截断的 'age'。所以必须并块才能拿到跨界标签；但并块一旦无界，版式稍一交叉
#: 就会把整页正文吞进来（实测图内文字占到整页 99%）。这里给纵向一个硬上限：
#: 允许够到图正下方约两行的图注，不允许够到远处段落。
CAPTION_VERTICAL_ALLOWANCE = 40.0
#: 单个文字块超过页面这个比例就不参与并块。
#:
#: 通栏正文段落与图重叠时，并进来就等于把整页文字当成了图注 —— 实测这正是无界并块
#: 把图内文字滚到整页 99% 的主要来源。
MAX_BLOCK_AREA_RATIO = 0.15
#: 合并时的邻接容差，与 PDF 单位（点）一致。
MERGE_PADDING = 2.0
#: 团里真正的图形元素占团面积的下限。低于此值的团只是「一堆文字矩形」。
VISUAL_COVERAGE_RATIO = 0.25

_SURROGATE_RANGES = ((0xD800, 0xE000),)


def sanitize_text(text: str) -> str:
    """丢掉孤立代理字符。

    MuPDF 对数学字母数字符号（U+1D400 段，公式里常见）按 UTF-16 半字吐出来，截断后
    就成了孤立代理项。它既不能编码成 UTF-8，也会让下游 ``Path.write_text`` 直接抛
    ``UnicodeEncodeError`` —— 实测在信息论课件上必然触发，与图片功能无关。

    分两段跳过来保住两侧的正常字符：整串 ``encode('utf-8','replace')`` 会把紧邻代理项
    的前一个字节也吃掉（"symbo" 变成 "sym?"），而 ``surrogatepass`` 又会把合法的
    补充平面字符（U+1D44E 这类真正的数学斜体字母）拆坏。
    """
    if not any(
        low <= ord(char) < high for char in text for low, high in _SURROGATE_RANGES
    ):
        return text
    parts: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if any(low <= ord(char) < high for low, high in _SURROGATE_RANGES):
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return "".join(parts)


@dataclass(frozen=True)
class FigureRaster:
    """团里包含的一张位图。"""

    xref: int
    width: int
    height: int
    #: MuPDF 的位图内容摘要，用来识别跨页重复的模板元素。
    digest: str


@dataclass(frozen=True)
class FigureRegion:
    """一页里的一张候选插图。"""

    page_number: int
    #: ``(x0, y0, x1, y1)``，PDF 点。
    bbox: tuple[float, float, float, float]
    area_ratio: float
    #: 图内文字层的原文（图注、轴名、刻度、标注）。空字符串表示图内没有可检索文字，
    #: 这类图只能靠 OCR 或视觉模型补内容。
    text: str
    rasters: tuple[FigureRaster, ...] = ()

    @property
    def has_text(self) -> bool:
        return bool(_HAS_TEXT.search(self.text))


@dataclass
class FigureStats:
    """一页的判定过程，用于诊断与阈值校准。"""

    candidates: int = 0
    excluded_as_background: int = 0
    rejected_as_text_boxes: int = 0
    rejected_too_small: int = 0
    figures: list[FigureRegion] = field(default_factory=list)


#: 「图内是否已有可检索文字」的判据，刻意宽松。
#:
#: 这个属性的用途是「这张图还要不要送去 OCR 补内容」，所以宁可多认几个短 token 也不要
#: 漏判：状态转移图里的状态名就是 `00` / `01` / `10` / `11`，全是两位数字。用
#: 「至少 3 个字母的词」这种严格判据会把整张图判成无文字。
_HAS_TEXT = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]")


@dataclass
class _Piece:
    rect: pymupdf.Rect
    is_visual: bool
    raster: FigureRaster | None = None


def _touches_page_edge(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> bool:
    tolerance = BACKGROUND_EDGE_TOLERANCE
    return (
        rect.x0 <= page_rect.x0 + tolerance
        or rect.y0 <= page_rect.y0 + tolerance
        or rect.x1 >= page_rect.x1 - tolerance
        or rect.y1 >= page_rect.y1 - tolerance
    )


def _strip_bands(
    pieces: list[_Piece], page_rect: pymupdf.Rect
) -> tuple[list[_Piece], int]:
    """剔除「多条等宽横带堆叠」这种版式骨架。

    这条判据解决的是并查集的**链式合并**：幻灯片的内容区块底色/虚线框是十几条等宽横带
    （实测 `06-Source-Coding` p45 有 14 条 `[35, y, 685, y+81]`，横贯整页、高 13%），
    彼此邻接于是被串成一条，最终并出一个占页面 87.7% 的「图」—— 那不是图，是版式的骨架，
    它的并集就是整块内容区。

    **为什么不能简单按「宽而扁」剔。** 单看形状会误杀正当的宽图：时间线、宽示意图、
    甘特图都是宽而扁的，实测用例里 480 点宽 × 40 点高的折线就是合法的图。版式骨架的
    特征不是「宽」，而是**重复**：两条以上横带同宽、同厚、纵向排列。图不会这样堆。

    只保留其中最高的一条（最可能承载内容的那条），其余剔除。
    """
    page_width = abs(page_rect.width)
    page_height = abs(page_rect.height)
    if not page_width or not page_height:
        return pieces, 0
    wide = [
        piece
        for piece in pieces
        if piece.rect.width / page_width >= BAND_SPAN_RATIO
        and piece.rect.height / page_height <= BAND_THICKNESS_RATIO
    ]
    if len(wide) < BAND_MIN_COUNT:
        return pieces, 0
    wide.sort(key=lambda piece: piece.rect.height, reverse=True)
    # 最高的一条留作内容候选，其余同宽横带剔除。
    dropped = {id(piece) for piece in wide[1:]}
    return [piece for piece in pieces if id(piece) not in dropped], len(dropped)


def _is_background(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> bool:
    """模板底色、页眉横幅、页脚分隔线 —— 版式元素，不是图形。

    课件的整块背景是一个 `draw_rect`，面积接近整页，靠面积阈值拦不住它 —— 实测它会
    在每页稳定贡献一个假图形。判别条件都只看**位置与形状**，不涉及内容语义：

    * **面积过半 / 贴页边** = 整块背景（整页底色、整宽色带）。
    * **长宽比 ≥ 10 的长条** = 分隔线、整宽横幅。
    * **页眉 / 页脚带内的扁平块**（宽高比 ≥ 8 且高度不足页面 8%）。

    **为什么前两条都要求贴页边，第三条不要求。** 只看长宽比或只看面积会误杀正当的图：
    宽流程图、时间线、甘特图的长宽比都在 10:1 以上，教材的整页插图面积也过半，而它们
    都在图区内、不接触页边。但页眉横幅**恰恰不贴页边** —— 实测课件标题横幅左边距 45 点、
    宽高比 9.2，两条贴边规则都放它过去，于是每页多出一个假图。位置（页眉/页脚带）才是
    这类元素的判据。
    """
    page_area = abs(page_rect.get_area())
    if not page_area:
        return False
    width, height = rect.width, rect.height
    if min(width, height) <= 0:
        return False
    aspect = max(width, height) / min(width, height)
    full_width = abs(page_rect.width)
    full_height = abs(page_rect.height)

    if _touches_page_edge(rect, page_rect):
        if rect.get_area() / page_area >= BACKGROUND_AREA_RATIO:
            return True
        if aspect >= STRIP_ASPECT_RATIO:
            return True
        if full_width and full_height and (
            width / full_width >= BACKGROUND_SPAN_RATIO
            or height / full_height >= BACKGROUND_SPAN_RATIO
        ):
            return True

    if not full_height:
        return False
    band = full_height * HEADER_FOOTER_BAND_RATIO
    in_band = rect.y1 <= page_rect.y0 + band or rect.y0 >= page_rect.y1 - band
    if not in_band:
        return False
    if height / full_height > HEADER_FOOTER_MAX_HEIGHT_RATIO:
        return False
    if not full_width or width / full_width < HEADER_FOOTER_MIN_WIDTH_RATIO:
        return False
    return aspect >= HEADER_FOOTER_ASPECT_RATIO


def _is_visual_drawing(drawing: dict) -> bool:
    """这条矢量路径画的是图形，还是只是个文字框。

    判据只看**原语种类**：全是矩形（``re``）就是文字/版式容器；一旦出现曲线、折线、
    圆、四边形，就说明画的是图形。**不要在这里看 ``fill``** —— 带底色填充的文本矩形
    会让整块公式区被误判成一张插图。
    """
    kinds = {item[0] for item in (drawing.get("items") or []) if item}
    return bool(kinds - {"re"})


def _merge(pieces: list[_Piece]) -> list[tuple[pymupdf.Rect, list[_Piece]]]:
    """把互相邻接的元素并成团。

    **配对用网格分桶，不是双重循环。** 双重循环在课件上够快（每页几十条路径），在教材
    页上直接卡死：一页几百条路径就是几万次比较。按「最大元素尺寸」分桶后只需检查自身与
    相邻的 9 个桶 —— 相交的两元素必然落在同一或相邻桶里，结果与双重循环一致。
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
        cell = max(max(piece.rect.width, piece.rect.height) for piece in pieces)
        cell = max(cell, 1.0)
        buckets: dict[tuple[int, int], list[int]] = {}
        for index, piece in enumerate(pieces):
            key = (int(piece.rect.x0 // cell), int(piece.rect.y0 // cell))
            buckets.setdefault(key, []).append(index)
        for (column, row), members in buckets.items():
            neighbours: list[int] = []
            for delta_column in (-1, 0, 1):
                for delta_row in (-1, 0, 1):
                    neighbours.extend(
                        buckets.get((column + delta_column, row + delta_row), [])
                    )
            for index in members:
                for other in neighbours:
                    if other > index and pieces[index].rect.intersects(pieces[other].rect):
                        union(index, other)

    grouped: dict[int, list[_Piece]] = {}
    for index, piece in enumerate(pieces):
        grouped.setdefault(find(index), []).append(piece)
    merged: list[tuple[pymupdf.Rect, list[_Piece]]] = []
    for members in grouped.values():
        union_rect = members[0].rect
        for piece in members[1:]:
            union_rect = union_rect | piece.rect
        merged.append((union_rect, members))
    return merged


def _text_blocks(page: pymupdf.Page) -> list[pymupdf.Rect]:
    blocks = page.get_text("blocks")
    return [
        pymupdf.Rect(block[0], block[1], block[2], block[3])
        for block in blocks
        if isinstance(block[4], str) and block[4].strip()
    ]


def caption_clip(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    """取图内文字时使用的范围：**有界**地并入邻近文字块。

    两件事同时要成立，缺一个都会出错：

    * **必须并块。** MuPDF 按词的**起点**决定是否纳入 clip，起点在范围外就整块丢掉 ——
      实测 'Voltage (mV)' 起点距图形左边界 58 点时，固定外扩要到 40 点才吐出被截断的
      'age'。只外扩不并块，图两侧的轴标签会整条丢掉。
    * **并出的范围必须有界。** 无界并块会把版式交叉处的正文整段吞进来：实测 170 张图的
      文字因此增长，新增字符中位数 85、最大 619，图内文字占到整页文字的 39%（最高 99%）。
      那不是图注，是把整篇文章灌进图的 chunk。

    所以并集要再与 :data:`CAPTION_VERTICAL_ALLOWANCE` 限定的区域取交 —— 横向跟着文字块
    走到标签起点，纵向只允许够到图正下方约两行。
    """
    pad = CAPTION_SEARCH_PADDING
    region = rect + (-pad, -pad, pad, pad)
    left, top, right, bottom = region.x0, region.y0, region.x1, region.y1
    allowance = CAPTION_VERTICAL_ALLOWANCE
    for block in _text_blocks(page):
        if not block.intersects(rect):
            continue
        if block.get_area() >= abs(page.rect.get_area()) * MAX_BLOCK_AREA_RATIO:
            # 整块正文（通栏段落）不参与：它和图重叠时并进来就是整页文字。
            continue
        left = min(left, block.x0)
        right = max(right, block.x1)
        top = max(rect.y0 - allowance, min(top, block.y0))
        bottom = min(rect.y1 + allowance, max(bottom, block.y1))
    return pymupdf.Rect(left, top, right, bottom)


def extract_figure_text(page: pymupdf.Page, rect: pymupdf.Rect) -> str:
    """图内的文字层：图注、轴名、刻度、标注。

    这是**零成本**的语义来源，也是唯一准确的来源 —— 它抄的是原文，不是模型的转述。
    覆盖不了图形语义（一张纯像素的电路图读不出"这是放大电路"），但能覆盖图里的每一个
    数据标签。实测教材里 57.4% 的矢量图带文字。

    取文字的范围由 :func:`caption_clip` 决定，原因见那里的注释。
    """
    return sanitize_text(page.get_text("text", clip=caption_clip(page, rect))).strip()


def detect_figures(
    page: pymupdf.Page,
    page_number: int,
    *,
    stats: FigureStats | None = None,
    images: Sequence[dict] = (),
) -> list[FigureRegion]:
    """列出这一页的候选插图。

    调用方应当先用 :mod:`app.document.page_kind` 判断页型：整页扫描件上正文与插图是
    同一张位图，这里找出来的「图」全是整页底图。本函数不做这个判断，因为它是纯几何
    逻辑，页型判断需要文字层密度 —— 两件事混在一起会让阈值互相掩盖。
    """
    page_rect = page.rect
    page_area = abs(page_rect.get_area())
    if not page_area:
        return []

    pieces: list[_Piece] = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not isinstance(rect, pymupdf.Rect) or rect.is_empty:
            continue
        if _is_background(rect, page_rect):
            if stats is not None:
                stats.excluded_as_background += 1
            continue
        pieces.append(
            _Piece(
                rect=rect + (-MERGE_PADDING, -MERGE_PADDING, MERGE_PADDING, MERGE_PADDING),
                is_visual=_is_visual_drawing(drawing),
            )
        )

    infos = tuple(images) if images else tuple(page.get_image_info(xrefs=True))
    if infos and any(not info.get("digest") for info in infos):
        # 调用方（分类阶段）查的图信息不带 digest，而重复位图去重需要它。同一个页面再查
        # 一次带 digest 的版本只在这里发生 —— 分类阶段自己用不到 digest，让它带着算
        # 会白付代价。
        by_xref = {
            int(info.get("xref") or 0): info
            for info in page.get_image_info(xrefs=True, hashes=True)
        }
        infos = tuple(
            by_xref.get(int(info.get("xref") or 0), info) for info in infos
        )
    for info in infos:
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if width * height < MIN_RASTER_PIXELS:
            continue
        rect = pymupdf.Rect(info["bbox"])
        if _is_background(rect, page_rect):
            if stats is not None:
                stats.excluded_as_background += 1
            continue
        pieces.append(
            _Piece(
                rect=rect + (-MERGE_PADDING, -MERGE_PADDING, MERGE_PADDING, MERGE_PADDING),
                is_visual=True,
                raster=FigureRaster(
                    xref=int(info.get("xref") or 0),
                    width=width,
                    height=height,
                    digest=(info.get("digest") or b"").hex()[:16],
                ),
            )
        )

    pieces, stripped = _strip_bands(pieces, page_rect)
    if stats is not None:
        stats.candidates += len(pieces)
        stats.excluded_as_background += stripped

    figures: list[FigureRegion] = []
    for union, members in _merge(pieces):
        if union.get_area() / page_area < MIN_AREA_RATIO:
            if stats is not None:
                stats.rejected_too_small += 1
            continue
        visual = sum(piece.rect.get_area() for piece in members if piece.is_visual)
        if visual / union.get_area() < VISUAL_COVERAGE_RATIO:
            if stats is not None:
                stats.rejected_as_text_boxes += 1
            continue
        figure = FigureRegion(
            page_number=page_number,
            bbox=(union.x0, union.y0, union.x1, union.y1),
            area_ratio=round(union.get_area() / page_area, 4),
            text=extract_figure_text(page, union),
            rasters=tuple(
                piece.raster for piece in members if piece.raster is not None
            ),
        )
        figures.append(figure)
        if stats is not None:
            stats.figures.append(figure)
    return figures


#: 一个位图重复多少次以上、且面积占比多小，就认定是模板标记（项目符号、水印、角标）。
#:
#: 两个条件缺一不可，实测依据（12 份信息论课件，260 个唯一位图摘要）：
#:
#: ==========  ==========  ==========  ==================
#: 总出现次数    跨页数      面积比      实际是什么
#: ==========  ==========  ==========  ==================
#: 25           5           0.049       3D 球体项目符号
#: 12           4           0.049       同一个
#: 8            2           0.049       同一个
#: 4~3          1           0.049       同一个（同一页重复 3~4 次）
#: 3            1           0.501       真图 —— 面积过半，**不能误杀**
#: ==========  ==========  ==========  ==================
#:
#: 只看"重复"会误杀"同一张状态转移图在第 5、12、20 页反复引用"这种正常情况；
#: 只看"面积小"会误杀凹向上/凹向下的曲线示意图（实测 422×153、面积比 0.228 的真内容）。
#: 两个一起看，才把这颗子弹与真内容分开。
MARKER_MIN_OCCURRENCES = 3
MARKER_MAX_AREA_RATIO = 0.1


def marker_digests(
    figures: list[FigureRegion],
    *,
    minimum_occurrences: int = MARKER_MIN_OCCURRENCES,
    maximum_area_ratio: float = MARKER_MAX_AREA_RATIO,
) -> set[str]:
    """挑出「模板标记」的位图摘要：反复出现、而且每次都只占页面一小块。

    这些不是插图：项目符号、工具水印（实测一张 `squoosh` logo）、模板角标。它们会跟着
    每一页出现，如果当作插图落盘，一篇 50 页的课件能产出几十张同样的装饰图，用户在图
    清单里看到的全是它们。

    调用方应当把这些摘要对应的图**丢掉**（见 `app/document/builtin_formats.py`）。
    返回摘要集合而不是图列表：同一张模板图在一页里可能出现多次，调用方按摘要过滤最直接。
    """
    small: dict[str, bool] = {}
    occurrences: dict[str, int] = {}
    for figure in figures:
        for raster in figure.rasters:
            if not raster.digest:
                continue
            occurrences[raster.digest] = occurrences.get(raster.digest, 0) + 1
            # 任一实例面积过大就不算标记：真图可能在一页里被引用多次。
            large = figure.area_ratio > maximum_area_ratio
            small[raster.digest] = small.get(raster.digest, True) and not large
    return {
        digest
        for digest, count in occurrences.items()
        if count >= minimum_occurrences and small.get(digest, False)
    }


def is_marker(figure: FigureRegion, digests: set[str]) -> bool:
    """这张图是否只由模板标记构成。

    只要它**全部**位图都是标记才算 —— 混合了真图的团不该整块丢掉。
    """
    if not figure.rasters or not digests:
        return False
    return all(raster.digest in digests for raster in figure.rasters if raster.digest)
