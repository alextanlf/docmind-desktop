"""OCR：坐标换算、阅读顺序、临时文件清理，以及一条真机识别质量用例。

坐标换算是这一层最容易错、又最难在事后发现的部分：错了不会报错，只会让引用定位指到
别的地方。所以它被单独拎出来测。

真机用例（``test_vision_reads_rendered_text``）只在 macOS 且编译产物存在时运行，
断言的是**识别内容本身**而不是「没崩」—— 把合成 PDF 渲染成图再识别，必须能读回原文。
"""
from __future__ import annotations

import sys

import pymupdf
import pytest

from app.document.ocr import (
    PURE_SCAN_MAX_DRAWINGS,
    OcrLine,
    OcrPage,
    RenderedImage,
    _probe_vision,
    _reading_order,
    _to_pdf_bbox,
    _vision_executable,
    find_backend,
    recognize,
    remap_to_page,
    render_page,
    render_region,
    scan_page_image,
    unavailable_reason,
    vision_availability,
)

PAGE_WIDTH = 600.0
PAGE_HEIGHT = 800.0


def _png_bytes(width: int, height: int) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pixmap.set_rect(pixmap.irect, (210, 215, 225))
    return pixmap.tobytes("png")


def _line(text: str, bbox: tuple[float, float, float, float], confidence: float = 1.0) -> OcrLine:
    return OcrLine(text=text, confidence=confidence, bbox=bbox)


def _page(document: pymupdf.Document) -> pymupdf.Page:
    return document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


# -- 坐标换算 -----------------------------------------------------------------


def test_normalized_bottom_left_becomes_top_left_pixels() -> None:
    """Vision 的坐标是归一化、原点左下；输出必须是像素点、原点左上。

    直接照搬会让每一处引用定位上下颠倒 —— 这种错不会抛异常，只会指错地方。
    """
    # 一个位于图片「上半部」的矩形：归一化 y=0.8（左下原点）→ 顶部 y=0.1。
    left, top, right, bottom = _to_pdf_bbox(0.25, 0.8, 0.5, 0.1, (1000.0, 800.0))
    # 归一化坐标是浮点，`(1 - 0.9) * 800` 得到 79.99999999999999 —— 必须用近似比较。
    assert left == pytest.approx(250.0)
    assert right == pytest.approx(750.0)
    assert top == pytest.approx(80.0)
    assert bottom == pytest.approx(160.0)


def test_full_height_box_maps_to_full_image() -> None:
    """整图矩形（y=0, height=1）必须映射成顶部 0、底部等于图高。"""
    left, top, right, bottom = _to_pdf_bbox(0.0, 0.0, 1.0, 1.0, (400.0, 300.0))
    assert (left, top, right, bottom) == (0.0, 0.0, 400.0, 300.0)


def test_bottom_of_image_maps_to_bottom_pixel() -> None:
    """贴近底边的矩形（归一化 y≈0）必须落在图片底部，而不是顶部。"""
    _, top, _, bottom = _to_pdf_bbox(0.0, 0.0, 0.1, 0.05, (1000.0, 1000.0))
    assert top > 900.0
    assert bottom == pytest.approx(1000.0)


# -- 阅读顺序 -----------------------------------------------------------------


def test_reading_order_is_top_to_bottom_then_left_to_right() -> None:
    """Vision 的返回顺序不是阅读顺序，拼接前必须重排。

    实测同一张状态转移图里 ``p(1/00)``、``01``、``= 0.2`` 交错输出；照原顺序拼出来的
    是一串语义错乱的文本。
    """
    lines = [
        _line("second", (500.0, 100.0, 560.0, 120.0)),
        _line("first", (100.0, 100.0, 160.0, 120.0)),
        _line("third", (100.0, 300.0, 160.0, 320.0)),
    ]
    assert [line.text for line in _reading_order(lines)] == ["first", "second", "third"]


def test_reading_order_keeps_nearly_level_lines_together() -> None:
    """同一行里基线略有高低的两段仍要按左右排，不能被错行拆开。"""
    lines = [
        _line("value", (400.0, 100.0, 460.0, 120.0)),
        _line("label", (100.0, 102.0, 160.0, 118.0)),
    ]
    assert [line.text for line in _reading_order(lines)] == ["label", "value"]


# -- 坐标回映射 ---------------------------------------------------------------


def test_region_render_maps_back_to_page_coordinates() -> None:
    """识别插图区域时图片被放大过，结果必须按比例换算回页面坐标。

    不换算的话，图内标注的坐标会停留在放大后的图片空间里，引用高亮会指到页面之外。
    """
    image = RenderedImage(
        pixels=b"",
        width=2000.0,
        height=1000.0,
        page_rect=(100.0, 200.0, 300.0, 300.0),
        page_number=1,
    )
    page = OcrPage(
        index=0,
        width=2000.0,
        height=1000.0,
        lines=(_line("label", (500.0, 250.0, 1000.0, 500.0)),),
    )
    (mapped,) = remap_to_page(page, image)
    # 图片宽 2000 映射到页面宽 200，缩放 0.1；图片高 1000 映射到页面高 100，缩放 0.1。
    assert mapped.bbox == pytest.approx((150.0, 225.0, 200.0, 250.0))


def test_full_page_render_maps_one_to_one() -> None:
    """整页渲染时页面矩形与图片矩形同尺寸，坐标不应被改动。"""
    image = RenderedImage(
        pixels=b"",
        width=600.0,
        height=800.0,
        page_rect=(0.0, 0.0, 600.0, 800.0),
        page_number=3,
    )
    page = OcrPage(index=0, width=600.0, height=800.0, lines=(_line("x", (10.0, 20.0, 30.0, 40.0)),))
    (mapped,) = remap_to_page(page, image)
    assert mapped.bbox == (10.0, 20.0, 30.0, 40.0)


def test_zero_sized_image_does_not_divide_by_zero() -> None:
    """退化输入（零尺寸页面）不该抛异常 —— 畸形 PDF 里出现过。"""
    image = RenderedImage(pixels=b"", width=0.0, height=0.0, page_rect=(0.0, 0.0, 0.0, 0.0), page_number=1)
    page = OcrPage(index=0, width=0.0, height=0.0, lines=(_line("x", (1.0, 2.0, 3.0, 4.0)),))
    assert remap_to_page(page, image)[0].bbox == (1.0, 2.0, 3.0, 4.0)


# -- 渲染 ---------------------------------------------------------------------


def test_render_page_covers_the_whole_page() -> None:
    document = pymupdf.open()
    page = _page(document)
    image = render_page(page)
    assert image.page_rect == (0.0, 0.0, PAGE_WIDTH, PAGE_HEIGHT)
    assert image.page_number == 1
    assert image.pixels.startswith(b"\x89PNG")


def test_render_region_covers_only_the_requested_box() -> None:
    document = pymupdf.open()
    page = _page(document)
    image = render_region(page, pymupdf.Rect(100.0, 200.0, 300.0, 320.0))
    assert image.page_rect == (100.0, 200.0, 300.0, 320.0)


def test_empty_image_list_is_not_an_error() -> None:
    """没有任何需要识别的图是正常情况，不该抛错。"""
    assert recognize([]) == []


# -- 能力声明 -----------------------------------------------------------------


def test_availability_reports_a_reason_not_an_exception() -> None:
    """不可用时要给出原因字符串 —— 与 formats 的 availability 同一条约定。

    新检出上二进制还没编译，所以 macOS 也可能不可用；此时原因必须**明确指向编译步骤**，
    而不是含糊的「不可用」。探针失败（例如架构不匹配）同样要给出原因。
    """
    reason = vision_availability()
    if sys.platform == "darwin":
        if reason is not None:
            assert "vision_ocr" in reason or "识别程序" in reason, reason
    else:
        assert reason is not None


def test_probe_rejects_a_binary_that_cannot_run(tmp_path) -> None:
    """探针必须能识别「文件在、但跑不起来」。

    这正是 arm64 二进制落到 Intel Mac 上的情形：文件存在、有执行位，一执行就被系统
    拒绝（bad CPU type）。只看文件属性会把它报成「可用」，用户要等到导入时才失败。
    """

    fake = tmp_path / "vision_ocr"
    fake.write_text("#!/bin/sh\necho 'Bad CPU type in executable' >&2\nexit 1\n")
    fake.chmod(0o755)

    ok, reason = _probe_vision(fake)

    assert ok is False
    assert reason is not None
    # 系统原文要带上：用户拿它去搜索，含糊的「不可用」帮不上忙。
    assert "Bad CPU type" in reason


def test_probe_accepts_a_working_binary() -> None:
    """探针对真产物必须通过 —— 否则可用性检查会把能用的机器也拦掉。"""
    executable = _vision_executable()
    if sys.platform != "darwin" or executable is None:
        pytest.skip("需要 macOS 且识别程序已编译")
    ok, reason = _probe_vision(executable)
    assert ok is True, reason


def test_backend_is_none_or_named_on_unsupported_platform() -> None:
    backend = find_backend()
    if backend is None:
        assert unavailable_reason() is not None
    else:
        assert backend.name in {"macos-vision", "windows-media-ocr"}


# -- 真机识别质量 -------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "darwin" or vision_availability() is not None,
    reason="需要 macOS 且随包分发的识别程序已就绪",
)
def test_vision_reads_rendered_text() -> None:
    """渲染出来的图必须能读回原文 —— 这是整条通路的端到端断言。

    断言识别**内容**而不是「没崩」：只测「返回了列表」的话，把坐标轴写反、把语言设成
    只认英文、渲染成空白图都能通过。
    """
    document = pymupdf.open()
    page = _page(document)
    page.insert_text((80, 200), "Recognition Check 4321", fontsize=24)

    image = render_page(page)
    pages = recognize([image])
    assert len(pages) == 1
    text = pages[0].text()
    assert "Recognition" in text
    # 数字也必须读对：语言纠错会把数字「顺手改对」，所以这一条同时守着
    # usesLanguageCorrection = false 这个设置。
    assert "4321" in text


@pytest.mark.skipif(
    sys.platform != "darwin" or vision_availability() is not None,
    reason="需要 macOS 且随包分发的识别程序已就绪",
)
def test_recognized_coordinates_land_inside_the_page() -> None:
    """识别坐标必须落在页面范围内。

    坐标换算写错（例如漏掉左下→左上的翻转）时，值会跑到页面外 —— 这条用例正是对着
    那个陷阱写的。
    """
    document = pymupdf.open()
    page = _page(document)
    page.insert_text((80, 200), "Recognition Check 4321", fontsize=24)

    image = render_page(page)
    pages = recognize([image])
    # recognize 返回的是**图片像素**坐标（200 dpi 渲染后是页面的 200/72 倍），
    # 必须经 remap_to_page 换回页面坐标才对得上页面尺寸。
    lines = [line for line in remap_to_page(pages[0], image) if "Recognition" in line.text]
    assert lines
    left, top, right, bottom = lines[0].bbox
    assert 0.0 <= left < right <= PAGE_WIDTH
    assert 0.0 <= top < bottom <= PAGE_HEIGHT


def test_pure_scan_page_returns_its_embedded_image() -> None:
    """纯扫描页要能直抽内嵌位图，而不是渲染页面。

    渲染一张整页扫描件等于把它重新光栅化：实测 190 ms/页，直抽只要 24 ms/页（约 8 倍），
    而且直抽用的是原生分辨率 —— 这类书内嵌位图是 96 dpi，按 150 dpi 渲染是插值放大，
    多出来的像素不是信息。
    """
    document = pymupdf.open()
    page = _page(document)
    page.insert_image(
        pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT),
        stream=_png_bytes(1439, 2035),
    )

    extracted = scan_page_image(page)

    assert extracted is not None
    # 页面矩形不变，所以坐标换算仍然对得上。
    assert extracted.page_rect == (0.0, 0.0, PAGE_WIDTH, PAGE_HEIGHT)
    assert (extracted.width, extracted.height) == (1439.0, 2035.0)


def test_page_with_vector_content_is_not_treated_as_a_pure_scan() -> None:
    """页面里还有矢量内容时不能直抽 —— 那些内容不在位图里，直抽会丢掉它们。

    这是安全的一侧：判不准就返回 None，调用方退回渲染。
    """
    document = pymupdf.open()
    page = _page(document)
    page.insert_image(
        pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT), stream=_png_bytes(1200, 1700)
    )
    # 超过 PURE_SCAN_MAX_DRAWINGS 条的矢量路径：说明页面不是「一张扫描图」那么简单。
    # 用 `page.draw_line` 而不是 `new_shape()`：后者在合成页面上不会落到页面里，
    # `get_drawings()` 会返回 0，于是这条用例测不到它要测的东西。
    for index in range(PURE_SCAN_MAX_DRAWINGS + 4):
        page.draw_line(
            pymupdf.Point(20, 100 + index * 10), pymupdf.Point(300, 100 + index * 10)
        )

    assert scan_page_image(page) is None


def test_two_stacked_page_images_are_not_a_pure_scan() -> None:
    """叠了两张整页图的页不直抽：哪一张才是内容无法判断（Feynman 那种形态）。"""
    document = pymupdf.open()
    page = _page(document)
    # 两张都必须过 90% 覆盖率，否则只数出一张、用例就测不到「叠图」这个条件。
    page.insert_image(pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT), stream=_png_bytes(580, 780))
    page.insert_image(pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT), stream=_png_bytes(1200, 1700))

    assert scan_page_image(page) is None


def test_extension_follows_the_payload_format() -> None:
    """临时文件扩展名必须跟着字节格式走。

    直抽出来的内嵌位图常是 JPEG，写成 `.png` 会让识别后端按 PNG 解析而失败。
    """
    document = pymupdf.open()
    page = _page(document)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 800, 1100))
    pixmap.set_rect(pixmap.irect, (200, 200, 200))
    page.insert_image(
        pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT), stream=pixmap.tobytes("jpg")
    )

    extracted = scan_page_image(page)

    assert extracted is not None
    assert extracted.extension in {"jpeg", "jpg"}


def test_disabling_figure_ocr_returns_empty_without_looking_for_a_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关掉识别时返回空结果，且不去找后端。

    **必须是空结果，不能是 `OcrUnavailable`。** 两者语义不同：不可用是"这台机器做不到"，
    调用方会退回文字层兜底；关掉是"用户选择不做"，每一页按"没有识别结果"处理正是预期。
    抛异常会让调用方走进"能力缺失"的分支。
    """
    from app.document import ocr

    def explode():
        raise AssertionError("关掉识别后不该再去探测后端")

    monkeypatch.setattr(ocr, "find_backend", explode)
    monkeypatch.setenv(ocr.FIGURE_OCR_ENV, "0")
    image = ocr.RenderedImage(
        pixels=b"\x89PNG", width=10.0, height=10.0, page_rect=(0.0, 0.0, 10.0, 10.0), page_number=1
    )

    assert ocr.recognize([image]) == []


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " off "])
def test_every_false_spelling_disables_recognition(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """关闭的写法要都认，大小写与空白不该决定行为。"""
    from app.document import ocr

    monkeypatch.setenv(ocr.FIGURE_OCR_ENV, value)
    assert ocr.figure_ocr_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
def test_anything_else_keeps_recognition_on(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """除明确关闭之外都保持开着 —— 默认必须是开，关掉会让截图型课件失去内容。"""
    from app.document import ocr

    monkeypatch.setenv(ocr.FIGURE_OCR_ENV, value)
    assert ocr.figure_ocr_enabled() is True


def test_recognition_is_on_when_the_variable_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.document import ocr

    monkeypatch.delenv(ocr.FIGURE_OCR_ENV, raising=False)
    assert ocr.figure_ocr_enabled() is True
