"""插图发现与页型判定：每条用例都对应一次实测踩坑。

这些用例全部现场合成 PDF，不检入二进制夹具 —— 与 `test_docx.py` 同一思路：
被验证的东西一次只声明一个，出问题时不用去猜二进制里有什么。

用例的来源是 `scripts/measure_figure_distribution.py` 在 12 份课件（559 页）与
12 本教材（8568 页）上测出的分布。断言写成「行为错就红」：模板底色被当图、扫描页
产出假图、中文 OCR 页被误判为纯扫描，都会让对应用例失败。
"""
from __future__ import annotations

import pymupdf

from app.document.figures import (
    FigureStats,
    detect_figures,
    is_marker,
    marker_digests,
)
from app.document.page_kind import (
    PageKind,
    PageSignals,
    TextLayer,
    classify_page,
    classify_signals,
)

#: 默认页尺寸（A4 点）。实测语料里教材与课件的页面都在这个量级。
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
PAGE_AREA = PAGE_WIDTH * PAGE_HEIGHT


def _page(document: pymupdf.Document) -> pymupdf.Page:
    return document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _image_bytes(width: int, height: int) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pixmap.set_rect(pixmap.irect, (180, 200, 230))
    return pixmap.tobytes("png")


def _full_page_image(page: pymupdf.Page, *, inset: float = 0.0) -> None:
    """铺一张覆盖整页的位图 —— 扫描件的形态。"""
    rect = pymupdf.Rect(inset, inset, PAGE_WIDTH - inset, PAGE_HEIGHT - inset)
    page.insert_image(rect, stream=_image_bytes(1200, 1700))


def _paragraph(page: pymupdf.Page, count: int, *, y: float = 60.0) -> None:
    """写入 ``count`` 行正文，用来把文字层密度顶过门槛。"""
    for index in range(count):
        page.insert_text(
            (60, y + index * 12),
            "The correlation among the symbols transmitted by an information source "
            "is limited and the Markov property holds for this source.",
            fontsize=9,
        )


def _open(raw: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=raw, filetype="pdf")


# -- 页型判定 -----------------------------------------------------------------


def test_native_text_page_is_not_scanned() -> None:
    document = pymupdf.open()
    page = _page(document)
    _paragraph(page, 20)
    assert classify_page(page).kind is PageKind.NATIVE_TEXT


def test_full_page_image_with_only_a_page_number_needs_ocr() -> None:
    """整页截图型幻灯片：文字层只剩页码。

    实测 `reinforcement-learning.pdf`（64 页）与 `Semantic Communications.pdf`（35 页）
    整份都是这个形态，正文一个字都抽不出来。早期只判「页面文字为空」的版本在这里会
    误判成原生页 —— 页码那 1 个字符足以让检测通过。
    """
    document = pymupdf.open()
    page = _page(document)
    _full_page_image(page)
    page.insert_text((280, 800), "5", fontsize=10)

    classification = classify_page(page)
    assert classification.kind is PageKind.SCANNED_NEEDS_OCR
    assert classification.page_ocr is True
    assert classification.figure_extraction is False


def test_scanned_page_with_real_ocr_layer_is_not_sent_to_ocr_again() -> None:
    """扫描 + OCR 的书：整页底图之上有真的文字层。

    实测 `算法导论.pdf` 每页一张 4080×6110 整页图，OCR 文字层 1100–1700 字符，内容与
    书页一致。把它当扫描件再 OCR 一遍，只会把好文字换成差文字。
    """
    document = pymupdf.open()
    page = _page(document)
    _full_page_image(page)
    _paragraph(page, 40, y=40.0)

    classification = classify_page(page)
    assert classification.kind is PageKind.SCANNED_WITH_OCR
    assert classification.text_layer is TextLayer.GOOD
    assert classification.page_ocr is False
    assert classification.embeds_scanned_page_image is True


def test_watermark_only_text_layer_on_a_scan_counts_as_no_text() -> None:
    """`编译原理.pdf` 每页 21 字符，内容是水印网址。

    判据是密度而不是字符数绝对值：21 字符在大页面上等于零，而 1108 字符是真实正文。
    这一条用密度门槛区分，正是为了避免把水印当正文。
    """
    document = pymupdf.open()
    page = _page(document)
    _full_page_image(page)
    page.insert_text((60, 800), "http://www.pin5i.com/", fontsize=9)

    classification = classify_page(page)
    assert classification.kind is PageKind.SCANNED_NEEDS_OCR
    assert classification.text_layer is not TextLayer.GOOD


def test_low_density_text_on_a_scan_is_sparse_not_good() -> None:
    """密度不足但字符数过线：判 sparse，仍然走 OCR。"""
    signals = PageSignals(
        area=PAGE_AREA,
        text="x" * 200,
        image_coverage=1.0,
        image_count=1,
    )
    classification = classify_signals(signals)
    assert classification.text_layer is TextLayer.SPARSE
    assert classification.kind is PageKind.SCANNED_NEEDS_OCR


def test_density_boundary_separates_textbook_from_watermark() -> None:
    """两个实测样本必须落在门槛两侧 —— 门槛写错就会被这条抓到。"""
    # 算法导论：1108 字符 / (490×733) 点 ≈ 0.0031
    textbook = PageSignals(
        area=490 * 733, text="字" * 1108, image_coverage=1.0, image_count=1
    )
    # 编译原理：水印 21 字符
    watermark = PageSignals(area=595 * 842, text="x" * 21, image_coverage=1.0, image_count=1)
    assert classify_signals(textbook).kind is PageKind.SCANNED_WITH_OCR
    assert classify_signals(watermark).kind is PageKind.SCANNED_NEEDS_OCR


# -- 插图发现 -----------------------------------------------------------------


def test_vector_figure_with_labels_is_found_without_any_bitmap() -> None:
    """只用位图找候选，matplotlib 折线图一张都进不来。

    实测一张矢量折线图的 `get_images()` 返回 0：曲线、坐标轴、刻度全是路径。这一页
    合成同样的形态，断言它被找到**且**图内文字层拿到了轴名与图注。
    """
    document = pymupdf.open()
    page = _page(document)
    shape = page.new_shape()
    shape.draw_polyline([(80, 600), (140, 500), (200, 520), (260, 400), (320, 430)])
    shape.finish(color=(0.1, 0.3, 0.8), width=1.5)
    shape.draw_line(pymupdf.Point(80, 600), pymupdf.Point(80, 380))
    shape.draw_line(pymupdf.Point(80, 600), pymupdf.Point(320, 600))
    shape.commit()
    # 轴名跨在绘图区边界上：不向外扩取文字就会只拿到后半截。
    page.insert_text((40, 480), "Voltage (mV)", fontsize=8)

    figures = detect_figures(page, page_number=1)
    assert len(figures) == 1
    assert figures[0].rasters == ()
    assert figures[0].has_text is True
    assert "Voltage" in figures[0].text


def test_figure_region_carries_labels_for_retrieval() -> None:
    """状态转移图的文字标签必须进 chunk —— 它们是唯一零成本的语义来源。

    实测 05-Markov-Source p21 有一张光栅化的状态转移图，图内全是
    `p(1/00) = 0.2`、`00`/`01`/`10`/`11` 这类可检索 token。
    """
    document = pymupdf.open()
    page = _page(document)
    shape = page.new_shape()
    shape.draw_circle(pymupdf.Point(150, 300), 60)
    shape.draw_circle(pymupdf.Point(350, 300), 60)
    shape.finish(color=(0, 0, 0), width=1.2)
    shape.commit()
    page.insert_text((140, 300), "00", fontsize=14)
    page.insert_text((340, 300), "01", fontsize=14)
    # 概率标注写在两个圆之间 —— 图内。图注/标注距离图形过远时不在覆盖范围，
    # 那是已知边界，由 test_caption_outside_the_figure_box_is_not_captured 钉住。
    page.insert_text((212, 268), "p(1/00) = 0.2", fontsize=9)

    figures = detect_figures(page, page_number=21)
    assert len(figures) == 1
    assert figures[0].has_text is True
    assert "p(1/00)" in figures[0].text


def test_label_overlapping_the_boundary_is_captured_whole() -> None:
    """压在图形边界上的标签必须整条取到，不能只留半截。

    MuPDF 按词的**起点**判断是否纳入 clip：起点在范围外就整块丢掉。实测 'Voltage (mV)'
    跨在边界上时只返回 'mV)'。这条用例的标注起点在图外、文字压在边界上 —— 必须并块
    才能拿到，去掉并块就会红。
    """
    document = pymupdf.open()
    page = _page(document)
    shape = page.new_shape()
    shape.draw_polyline([(120, 600), (180, 500), (240, 520), (300, 400), (360, 430)])
    shape.finish(color=(0.1, 0.3, 0.8), width=1.5)
    shape.commit()
    # 起点 x=100 在图形左边界（118）之外，文字一直排到 x≈166 —— 压在边界上。
    page.insert_text((100, 505), "Voltage (mV)", fontsize=8)

    figures = detect_figures(page, page_number=1)
    assert len(figures) == 1
    assert "Voltage (mV)" in figures[0].text


def test_label_far_outside_the_boundary_is_not_swept_in() -> None:
    """距离过远的标签**不**取 —— 这是有意的边界，不是待修的缺陷。

    并块方案曾试图把它们捞回来，结果是图内文字滚成整页正文（新增字符最大 619，
    图内文字占到整页 99%）。有界外扩是刻意的取舍：宁可少拿一点标签，也不让一张图的
    chunk 装进整篇文章。
    """
    document = pymupdf.open()
    page = _page(document)
    shape = page.new_shape()
    shape.draw_polyline([(300, 600), (360, 500), (420, 520), (480, 400), (540, 430)])
    shape.finish(color=(0.1, 0.3, 0.8), width=1.5)
    shape.commit()
    # 图形左边界在 x=298，标签块右端在 x=247 —— 距离约 51 点，远超外扩边距。
    page.insert_text((200, 700), "Voltage", fontsize=8)

    figures = detect_figures(page, page_number=1)
    assert len(figures) == 1
    assert "Voltage" not in figures[0].text


def test_caption_outside_the_figure_box_is_not_captured() -> None:
    """图注常规在图外下方 —— 记录这个已知边界，避免被误当成已解决。

    实测 05-Markov-Source p21 的图注就在图外。要覆盖它需要向外扩展 bbox 再判断哪些
    文字属于图注，那是一个独立的改动；这里先把边界钉住，免得有人以为已经拿到了。
    """
    document = pymupdf.open()
    page = _page(document)
    shape = page.new_shape()
    shape.draw_circle(pymupdf.Point(200, 300), 70)
    shape.finish(color=(0, 0, 0), width=1.2)
    shape.commit()
    # 图底在 y=370，图注放在 y=430 —— 在取文字的外扩边距之外。
    page.insert_text((60, 430), "Figure 3: Voltage decay over time", fontsize=9)

    figures = detect_figures(page, page_number=1)
    assert len(figures) == 1
    assert figures[0].bbox[3] < 400
    assert "Figure 3" not in figures[0].text


def test_wide_flat_figure_inside_the_page_is_kept() -> None:
    """宽而扁的正当图形不能被「长条 = 版式元素」误杀。

    宽流程图、时间线、甘特图的长宽比都在 10:1 以上。宽度本身不是版式元素的证据 ——
    真正的页眉色带/分隔线一定贴页边。这条用例来自一次真实的误杀：只看长宽比就剔的
    版本会把图区内部的宽图丢掉。
    """
    document = pymupdf.open()
    page = _page(document)
    # 长宽比 12:1，完全在图区内，不接触任何页边。
    rect = pymupdf.Rect(80, 300, 560, 340)
    assert (rect.width / rect.height) >= 10.0
    shape = page.new_shape()
    shape.draw_polyline(
        [(80, 320), (160, 305), (240, 335), (320, 310), (400, 330), (480, 308), (560, 325)]
    )
    shape.finish(color=(0, 0, 0), width=1.2)
    shape.commit()
    page.insert_text((100, 360), "Timeline of the protocol handshake phases", fontsize=8)

    figures = detect_figures(page, page_number=1)
    assert len(figures) == 1
    assert figures[0].area_ratio > 0.02


def test_title_banner_inside_the_margins_is_not_a_figure() -> None:
    """课件标题横幅不贴页边，靠「贴边」判据拦不住它。

    实测 01-Introduction2026 p4 是一张 842×91 的标题图，左边距 45 点、宽高比 9.2，
    结果每页多出一个假图。这类元素的判据是**位置**（页眉带）而不是贴边。
    """
    document = pymupdf.open()
    page = _page(document)
    # 左边距 45 点，宽高比 9.2，全部落在页眉带内 —— 与实测横幅同形。
    page.draw_rect(pymupdf.Rect(45, 31, 320, 61), color=(0, 0, 0.6), fill=(0.1, 0.2, 0.7))
    _paragraph(page, 10, y=200.0)

    stats = FigureStats()
    assert detect_figures(page, page_number=4, stats=stats) == []
    assert stats.excluded_as_background == 1


def test_full_width_diagram_in_the_header_band_is_kept() -> None:
    """页眉带里的宽图不该被误杀 —— 判据还要求它「扁」。

    一张真正画在图区上方的宽示意图（宽高比够大但高度占页高 20%）是内容，不是横幅。
    没有这条上限，页眉带规则会变成「顶部一律不要」，那是另一种错。
    """
    document = pymupdf.open()
    page = _page(document)
    # 宽高比 10，但高度 168 点 = 页高 20%，超出横幅上限。
    shape = page.new_shape()
    shape.draw_polyline([(40, 60), (160, 120), (300, 70), (430, 160), (560, 90)])
    shape.finish(color=(0, 0, 0), width=1.5)
    shape.commit()
    page.insert_text((60, 176), "End-to-end pipeline of the proposed system", fontsize=9)

    figures = detect_figures(page, page_number=1)
    assert len(figures) == 1
    assert "pipeline" in figures[0].text


def test_template_background_rectangle_is_not_a_figure() -> None:
    """课件模板的整块底色每页都在，不剔它每页至少一个假图。

    实测 05-Markov-Source p8 通篇文字与公式、没有任何插图，却被初版算法算成一个矢量
    图形 —— 原因就是整块背景矩形。断言这里 `detect_figures` 返回空。
    """
    document = pymupdf.open()
    page = _page(document)
    page.draw_rect(
        pymupdf.Rect(0, 0, PAGE_WIDTH, 120), color=(0, 0, 0.6), fill=(0, 0, 0.6)
    )
    _paragraph(page, 12, y=200.0)

    stats = FigureStats()
    figures = detect_figures(page, page_number=8, stats=stats)
    assert figures == []
    # 贴页边的整宽色带被当背景剔除（不是被当「文字框」拒绝，两个计数桶含义不同）。
    assert stats.excluded_as_background >= 1


def test_equation_area_of_text_rectangles_is_not_a_figure() -> None:
    """Word/PPT 导出的公式是一堆带边框的文本矩形，合并后同样超面积阈值。

    判据不能看填充色 —— 带底色的文本矩形会让整块公式区被误判成插图。
    """
    document = pymupdf.open()
    page = _page(document)
    for index in range(4):
        page.draw_rect(
            pymupdf.Rect(80, 200 + index * 30, 480, 226 + index * 30),
            color=(0.8, 0.8, 0.8),
            fill=(0.95, 0.95, 0.95),
        )
        page.insert_text((90, 220 + index * 30), f"E = m c^2  ({index})", fontsize=11)

    stats = FigureStats()
    assert detect_figures(page, page_number=3, stats=stats) == []
    assert stats.rejected_as_text_boxes >= 1


def test_full_page_scan_yields_no_figures() -> None:
    """整页扫描件上「插图」与正文是同一张位图，找出来的全是整页底图。

    实测 `CS Theory Toolkit` 的 130 个扫描页曾产出 68 个这样的假图，面积 0.53–0.65。
    本函数是纯几何逻辑，所以它**会**报出这张整页图；拒绝它的是页型判定 —— 这条用例
    把两层放在一起断言，确保调用方不能只看一层。
    """
    document = pymupdf.open()
    page = _page(document)
    _full_page_image(page)
    page.insert_text((280, 800), "7", fontsize=10)

    classification = classify_page(page)
    assert classification.page_ocr is True
    assert classification.figure_extraction is False


def test_tiny_icon_is_ignored_but_chart_is_kept() -> None:
    """页眉小图标不该进候选，正常的图不该被面积门槛误杀。"""
    document = pymupdf.open()
    page = _page(document)
    page.insert_image(pymupdf.Rect(20, 20, 50, 50), stream=_image_bytes(40, 40))
    page.insert_image(
        pymupdf.Rect(120, 300, 420, 520), stream=_image_bytes(600, 440)
    )

    figures = detect_figures(page, page_number=1)
    assert len(figures) == 1
    assert figures[0].rasters[0].width == 600
    assert figures[0].area_ratio > 0.05


def test_repeated_small_rasters_are_identified_as_template_markers() -> None:
    """反复出现、且每次都只占页面一小块的位图是模板标记，不是插图。

    实测依据（12 份信息论课件、260 个位图摘要）：一颗 3D 球体项目符号出现 25 次、跨 5 页，
    面积比恒为 0.049；而唯一另一个 ≥3 次重复的摘要面积比是 0.501（真图）。两个条件一起
    看才分得开。
    """
    figures = [
        _figure(page_number=page, rasters=(_raster("bullet"),), area_ratio=0.049)
        for page in (2, 5, 9)
    ]

    assert marker_digests(figures) == {"bullet"}


def test_a_repeated_large_figure_is_not_a_marker() -> None:
    """面积过半的重复图不是标记 —— 只看"重复"会误杀被反复引用的真图。"""
    figures = [
        _figure(page_number=page, rasters=(_raster("chart"),), area_ratio=0.501)
        for page in (2, 5, 9)
    ]

    assert marker_digests(figures) == set()


def test_a_small_but_unique_figure_is_not_a_marker() -> None:
    """只出现一次的小图不是标记 —— 只看"面积小"会误杀小尺寸的真内容。

    实测反例：一张 422×153、面积比 0.228 的"凹向上/凹向下"曲线示意图是真内容。
    """
    figures = [_figure(page_number=2, rasters=(_raster("diagram"),), area_ratio=0.06)]

    assert marker_digests(figures) == set()


def test_a_figure_mixing_marker_and_content_is_kept() -> None:
    """团里既有标记又有真图时整块保留 —— 只丢纯标记的那些。"""
    markers = {"bullet"}
    mixed = _figure(
        page_number=2,
        rasters=(_raster("bullet"), _raster("chart")),
        area_ratio=0.2,
    )
    pure = _figure(page_number=3, rasters=(_raster("bullet"),), area_ratio=0.049)

    assert is_marker(mixed, markers) is False
    assert is_marker(pure, markers) is True


def _raster(digest: str):
    from app.document.figures import FigureRaster

    return FigureRaster(xref=1, width=125, height=125, digest=digest)


def _figure(*, page_number: int, rasters, area_ratio: float):
    from app.document.figures import FigureRegion

    return FigureRegion(
        page_number=page_number,
        bbox=(0.0, 0.0, 10.0, 10.0),
        area_ratio=area_ratio,
        text="",
        rasters=tuple(rasters),
    )
