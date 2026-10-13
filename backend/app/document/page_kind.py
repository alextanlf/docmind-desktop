"""分页类型：先判断这一页的文字层能不能用，再决定怎么抽内容。

**为什么需要这一层。** 入口解析器原本只有一个假设 —— 页面上有可读文字。这个假设在
真实语料里对三种页都不成立，而它们的正确处理方式**互相排斥**：

* 幻灯片导出成整页截图（`reinforcement-learning.pdf` 24 页、`Semantic Communications.pdf`
  35 页）：文字层只剩页码，正文一个字都抽不出来。
* 纯扫描书（`统计学习方法.pdf` 487 页、`编译原理.pdf` 425 页之类）：正文与插图在同一张
  整页位图里，文字层要么是 0 字符，要么只有水印（`编译原理` 是 21 字符的网址）。
* 扫描 + OCR 的书（`算法导论.pdf` 794 页、`Introduction to Linear Algebra` 584 页）：
  每页也有一张整页位图，**但文字层是真的**。把它当扫描件去 OCR 会把好文字换成差文字。

把这三类混为一谈的代价是实测的：`算法导论` 被当成「每页一张整页图」时，150 页里
报出 0 个图形、0 个独立插图；而 `CS Theory Toolkit` 的 130 个扫描页被当成普通页时，
产出 68 个「插图」—— 全都是面积 0.53–0.65 的整页底图。

**判据是密度，不是字符数。** `编译原理` 的 21 字符和 `算法导论` 的 1108 字符的区别不在
绝对值上，而在「每平方点有多少字」：前者 0.00006、后者 0.0031，中间有两个数量级。
用绝对值会在大页面上误判。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

import pymupdf

#: 文字层能给出多少内容。
TextQuality = str


class TextLayer(str, Enum):
    """这一页文字层的可用程度。"""

    #: 真实正文，可以直接抽。
    GOOD = "good"
    #: 有文字但太稀疏，等于没有 —— 实测 21 字符的水印页落在这里。
    SPARSE = "sparse"
    #: 一个字都没有。
    NONE = "none"


class PageKind(str, Enum):
    """这一页属于哪一种，以及由此决定的提取策略。"""

    #: 文字由 PDF 自己绘制（原生电子书、LaTeX 排版）。抽文本层，正常找插图。
    NATIVE_TEXT = "native_text"
    #: 文字层没有可用内容（纯扫描、整页截图）→ **整页 OCR**，不产出独立插图。
    SCANNED_NEEDS_OCR = "scanned_needs_ocr"
    #: 扫描得到、但已有可用的 OCR 文字层 → 直接用文字层；整页底图不是插图。
    SCANNED_WITH_OCR = "scanned_with_ocr"


#: 图片覆盖率不低于此值，就认为这一页「主体是图」。
SCANNED_MIN_COVERAGE = 0.4
#: 整页图页面上，每平方点的最少字符数才算文字层可用。
#: 实测分界：算法导论 1108 字符 / (490×733) 点 ≈ 0.0031；
#: 编译原理 21 字符 / 大页 ≈ 0.00006。取两者之间。
SCANNED_TEXT_MIN_DENSITY = 0.0015
#: 低于这么多字符的文字层在整页图上直接算「没有」。给页码、水印留一点余量。
SCANNED_TEXT_MIN_CHARS = 70
#: 一句话都没有、但图片也没覆盖多少的页（空白页、纯装饰页）。
_EMPTY_TEXT = re.compile(r"^\s*$")
#: 铺满整页的位图达到这个张数，就认为文字层不可信。理由见 `PageSignals.full_page_images`。
STACKED_PAGE_IMAGES = 2
#: 纯扫描件的判据：内嵌位图的宽度至少是页面宽度的这么多倍（即「每点几个像素」）。
#:
#: 用它替代「位图 bbox 占页面多少」：后者只能从 `get_image_info` 拿到，而那个查询实测
#: 扫描件上要 36.4 ms/页。实测纯扫描件该值都在 1.3 以上，门槛取 0.5 留足余量。
PURE_SCAN_MIN_PIXELS_PER_POINT = 0.5
#: 纯扫描页允许存在的矢量路径条数上限。超过它说明页面上还有文字/图形是矢量画的，
#: 那些内容不在位图里，抽图会丢掉它们。
PURE_SCAN_MAX_DRAWINGS = 8
#: 文字层字符数不超过这个值，才考虑「这一页可能是纯扫描件」。
#:
#: 用来把「正文 + 一张插图」的原生 PDF 挡在快路径之外：那种页也满足「单图」，但它的
#: 文字是页面的主体，不该走扫描件那条路（走了会让有文字的页再被 OCR 一遍）。
PURE_SCAN_MAX_TEXT_CHARS = 200
#: 每平方点的字符数达到这个值，就认定「文字层够密、不必再看图」。
#:
#: 实测扫描件的文字层密度上限约 0.002 字符/平方点（算法导论 1108 字符 / (490×733)），
#: 取 0.01 留出五倍余量，宁可多查一次 `get_image_info` 也不要误判原生 PDF。
#:
#: 命中时把 `image_coverage` 直接填成 `SCANNED_MIN_COVERAGE`：这个值只被
#: `is_scanned()` 用来判断，取一个「够大但不夸大」的数即可，不必真去算覆盖率。
DENSE_TEXT_CHARS_PER_POINT = 0.01


@dataclass(frozen=True)
class PageSignals:
    """分类所需的全部输入。

    刻意做成纯数据：分类逻辑因此可以在不打开 PDF 的情况下被单测，测试能直接构造
    「文字很少 + 图覆盖很大」这类在真实语料里罕见但决定行为的组合。
    """

    #: 页面面积（平方点）。为 0 时无法算密度，退化为不看密度。
    area: float
    text: str
    image_coverage: float
    image_count: int
    #: 铺满整页的位图张数。
    #:
    #: **≥ 2 是「文字层不可信」唯一可靠的信号。** 实测 Internet Archive 的
    #: `SURELY YOU'RE JOKING MISTER FEYNMAN`：每页叠了两张整页图，而它的文字层是被
    #: 损坏的 —— `Thad only come to watch`（I had only…）、`What I had intended to do w`
    #: （句子截断）、`Another guy 8ol up`（got）、结尾一整段被拆散重排。别的书都是
    #: 每页 0–1 张：Feynman 26/26 页命中，次高的一本只有 1/26，其余 11 本全是 0。
    #:
    #: 为什么不用词法指标（单字符词率、畸形词率）判断文字层好坏 —— 实测两者都**反向**：
    #: Feynman 的单字符词率 0.062、畸形词率 0.005，都低于文字层干净的算法导论
    #: （0.208 / 0.019）。原因是这种损坏是**删词、重排、把词换成另一个真词**，不产生
    #: 异常 token，任何基于 token 形状的指标都测不到它。
    full_page_images: int = 0

    #: 分类阶段已经确定的文字层结论。
    #:
    #: 纯扫描件的「文字层没有内容」是**由判据直接得出的**（单张整页位图 + 文字稀疏），
    #: 比按密度重算更准确，所以在这里显式记下来，避免 :func:`classify_signals` 用密度
    #: 覆盖掉它。
    text_layer_override: TextLayer | None = None
    #: 这一页已被确认是**纯扫描件**（单张整页位图 + 无矢量内容）。
    #:
    #: 由分类阶段用便宜查询判定，取图阶段直接照着用 —— 两边各自去查一遍同一件事会白付
    #: 一次 36.4 ms/页。
    pure_scan: bool = False
    #: 分类时已经算出的 `get_image_info` 结果，供后续取图/找图复用。
    #:
    #: 不参与比较与序列化（`compare=False`）：它只是缓存，两个信号在语义上相不相等与
    #: 这份列表无关。留着它是因为 `get_image_info` 实测要 36–142 ms/页，而分类与取图
    #: 各需要一次 —— 同一页调两遍等于白烧一次。
    images: tuple[dict, ...] = field(default=(), compare=False, repr=False)

    @classmethod
    def from_page(cls, page: pymupdf.Page) -> PageSignals:
        """收集分类所需的信号。

        **调用顺序是按实测成本排的，不是随意写的。** `pymupdf` 的三个查询在这个语料上
        差两个数量级（每页毫秒，实测）：

        ==================  ============  ==================
        页类型               get_images    get_image_info
        ==================  ============  ==================
        纯扫描               0.1           37.7
        原生文字（无图）       0.0            1.9
        ClearScan（多小图）   0.5           13.4
        ==================  ============  ==================

        所以先用近乎免费的 `get_images` 分派：

        * **一个位图都没有** → 不可能是扫描件，跳过 ``get_image_info``（省 1.9 ms/页）。
        * **文字层够密且只有一张图** → 直接返回。这一页已经有可用的 OCR 文字层，后面的
          取图/识别都用不上（省下整个 36–142 ms/页）。扫描件的文字层密度上限约
          0.002 字符/平方点，不会被误判成密。
        * 其余（纯扫描、ClearScan 这类多小图页、以及叠图页）才付 ``get_image_info``
          的代价。多图时**刻意不短路**：叠图正是「文字层不可信」的信号，那需要知道有几张
          图铺满整页。
        """
        area = abs(page.rect.get_area())
        text = page.get_text("text").strip()
        graphics = page.get_images(full=True)
        count = len(graphics)
        if not count:
            return cls(area=area, text=text, image_coverage=0.0, image_count=0)
        # 文字层够密且只有一张图 → 已是有可用文字层的扫描件，取图/识别都用不上。
        # 多图时不能短路：叠图正是「文字层不可信」的信号（见 full_page_images），
        # 而那一判定需要知道有几张图铺满整页，短路会把信号丢掉。
        if count == 1 and area and len(text) / area >= DENSE_TEXT_CHARS_PER_POINT:
            return cls(
                area=area,
                text=text,
                image_coverage=SCANNED_MIN_COVERAGE,
                image_count=1,
                pure_scan=True,
            )
        # 单图 + 文字稀疏 + 位图分辨率够整页 → 纯扫描件。
        #
        # 这一支绕开 `get_image_info`（实测扫描件上 36.4 ms/页），改用 `extract_image`
        # （0.1 ms）直接拿像素尺寸判断 —— 但只在**文字稀疏**时才走：原生 PDF 里
        # 「正文 + 一张插图」的页也满足「单图」，不能把它们误判成扫描件（那样会让原本
        # 有文字的页再被 OCR 一遍）。密文字页在上面已经返回了。
        if count == 1 and len(text) <= PURE_SCAN_MAX_TEXT_CHARS:
            width, _ = _extracted_size(page, graphics[0])
            if width and area and width / abs(page.rect.width) >= PURE_SCAN_MIN_PIXELS_PER_POINT:
                return cls(
                    area=area,
                    text=text,
                    image_coverage=SCANNED_MIN_COVERAGE,
                    image_count=1,
                    full_page_images=1,
                    pure_scan=True,
                    text_layer_override=TextLayer.NONE,
                )
        images = page.get_image_info(xrefs=True)
        coverage = 0.0
        full_page = 0
        if area:
            for info in images:
                image_area = pymupdf.Rect(info["bbox"]).get_area()
                coverage += image_area / area
                if image_area / area >= SCANNED_MIN_COVERAGE:
                    full_page += 1
        return cls(
            area=area,
            text=text,
            image_coverage=coverage,
            image_count=len(images),
            full_page_images=full_page,
            images=tuple(images),
        )


@dataclass(frozen=True)
class PageClassification:
    """一页的结论。``figure_extraction`` 与 ``page_ocr`` 互斥。"""

    kind: PageKind
    text_layer: TextLayer
    signals: PageSignals

    @property
    def figure_extraction(self) -> bool:
        """是否可以在这页上找独立插图。

        只有原生文字页可以。整页扫描件上「插图」和正文是同一张位图，找出来的所谓插图
        全是整页底图 —— 实测 `CS Theory Toolkit` 的 130 个扫描页产出 68 个这样的假图。
        """
        return self.kind is not PageKind.SCANNED_NEEDS_OCR

    @property
    def page_ocr(self) -> bool:
        """是否需要整页 OCR。"""
        return self.kind is PageKind.SCANNED_NEEDS_OCR

    @property
    def embeds_scanned_page_image(self) -> bool:
        """整页底图是否只是承载文字，不是插图（扫描 + OCR 的书）。"""
        return self.kind is PageKind.SCANNED_WITH_OCR

    @property
    def text_layer_trusted(self) -> bool:
        """这一页的文字层是否可信到可以直接用。

        叠了多张整页图的页不可信：那是「同一次扫描被存了多层」的骨架特征，而这种 PDF 的
        文字层实测是损坏的。调用方应当在 OCR 可用时改用 OCR，在不可用时仍然退回文字层
        —— 有缺陷的文字也好过什么都没有。
        """
        return self.signals.full_page_images < STACKED_PAGE_IMAGES


def _extracted_size(page: pymupdf.Page, graphic: tuple) -> tuple[int, int]:
    """内嵌位图的真实像素尺寸。取不到时返回 ``(0, 0)``。

    比 `get_image_info` 便宜三个数量级（实测 0.1 ms 对 36.4 ms/页）：后者要算每张图在
    页面上的位置，而这里只想知道这张图有多大。
    """
    try:
        extracted = page.parent.extract_image(int(graphic[0]))
    except (RuntimeError, ValueError, KeyError, TypeError, IndexError):
        return 0, 0
    return int(extracted.get("width") or 0), int(extracted.get("height") or 0)


def is_scanned(signals: PageSignals) -> bool:
    """这一页是否以一张（或几张）整页图为主体。

    只看覆盖率，不看文字 —— 否则「扫描 + OCR」的书会被归成原生 PDF。
    """
    return signals.image_coverage >= SCANNED_MIN_COVERAGE


def _text_layer(signals: PageSignals, *, verify: bool) -> TextLayer:
    if _EMPTY_TEXT.match(signals.text):
        return TextLayer.NONE
    if not verify or not signals.area:
        return TextLayer.GOOD
    if len(signals.text) <= SCANNED_TEXT_MIN_CHARS:
        return TextLayer.NONE
    if len(signals.text) / signals.area < SCANNED_TEXT_MIN_DENSITY:
        return TextLayer.SPARSE
    return TextLayer.GOOD


def classify_signals(signals: PageSignals) -> PageClassification:
    """把信号判成一类。

    ``verify`` 的含义是「文字层需要证明自己」：在整页图上，少量文字通常只是页码或
    水印，必须过密度门槛才算数；在非扫描页上不存在「整页底图」这个混淆项，
    有文字就是有文字。
    """
    scanned = is_scanned(signals)
    # 短路路径（纯扫描件）自己声明了文字层：那是「页面文字层只有页码或水印」的判断，
    # 比按密度重算更准确，不能覆盖掉。
    quality = signals.text_layer_override or _text_layer(signals, verify=scanned)
    if not scanned:
        # 非扫描页不因为「文字少」就判去 OCR：没有整页底图可识别，OCR 只会把同一片
        # 空白再读一遍。空白页与纯装饰页由调用方按「无内容」处理。
        kind = PageKind.NATIVE_TEXT
    elif quality is TextLayer.GOOD and signals.full_page_images < STACKED_PAGE_IMAGES:
        kind = PageKind.SCANNED_WITH_OCR
    else:
        # 文字层不可用**或**叠了多张整页图（文字层不可信）→ 走 OCR。
        kind = PageKind.SCANNED_NEEDS_OCR
    return PageClassification(kind=kind, text_layer=quality, signals=signals)


def classify_page(page: pymupdf.Page) -> PageClassification:
    return classify_signals(PageSignals.from_page(page))
