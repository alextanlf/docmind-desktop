"""PDF 解析：按页型分派，以及图内文字并入正文。

这一组用例守的是**内容不再被静默丢掉**：旧实现只认 ``page.get_text()``，没有文字层的页
直接消失。实测语料里这类页占 24.5%（课件）与 33.7%（教材），其中包括整份都是幻灯片截图
的文档 —— ``reinforcement-learning.pdf`` 64 页里旧实现只留下 8 页。

OCR 在用例里被打桩，所以这些断言测的是**解析器的分派与合并逻辑**，不是识别质量。
识别质量由 ``test_ocr.py`` 的真机用例覆盖。
"""
from __future__ import annotations

import pymupdf
import pytest

from app.document import ocr
from app.document.ocr import OcrLine, OcrPage, OcrUnavailable
from app.document.parser import DocumentParser
from app.schemas.imports import DownloadedDocument

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0


def _image_bytes(width: int, height: int) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pixmap.set_rect(pixmap.irect, (200, 210, 230))
    return pixmap.tobytes("png")


def _scanned_page(document: pymupdf.Document, page_number_text: str = "7") -> pymupdf.Page:
    """一整页位图 + 一个页码 —— 整页截图型幻灯片的形态。"""
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(
        pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT), stream=_image_bytes(1200, 1700)
    )
    page.insert_text((280, 800), page_number_text, fontsize=10)
    return page


def _text_page(document: pymupdf.Document, text: str) -> pymupdf.Page:
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    for index, line in enumerate(text.splitlines() or [text]):
        page.insert_text((60, 100 + index * 16), line, fontsize=10)
    return page


def _document(pdf: pymupdf.Document) -> DownloadedDocument:
    raw_bytes = pdf.tobytes()
    pdf.close()
    return DownloadedDocument(
        title="slides.pdf",
        source_url="https://docs.test/slides.pdf",
        media_type="application/pdf",
        raw_bytes=raw_bytes,
    )


def _fake_ocr(text_by_page: dict[int, str]):
    """打桩：按渲染顺序返回固定文本，并记录被识别的页数。"""
    calls: list[int] = []

    def fake_recognize(images):
        calls.append(len(images))
        pages = []
        for index, image in enumerate(images):
            text = text_by_page.get(index, "")
            pages.append(
                OcrPage(
                    index=index,
                    width=image.width,
                    height=image.height,
                    lines=tuple(
                        OcrLine(text=line, confidence=1.0, bbox=(10.0, 10.0 + 20 * row, 200.0, 26.0 + 20 * row))
                        for row, line in enumerate(text.splitlines())
                    ),
                )
            )
        return pages

    return fake_recognize, calls


def test_page_needing_ocr_is_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """整页截图页必须产出 section —— 旧实现在这里整页丢弃。

    这是本次改动要修的核心缺陷：`reinforcement-learning.pdf` 64 页里旧实现只留下 8 页。
    """
    fake, calls = _fake_ocr({0: "Grid world example\nAction space"})
    monkeypatch.setattr(ocr, "recognize", fake)

    pdf = pymupdf.open()
    _scanned_page(pdf)
    parsed = DocumentParser().parse(_document(pdf))

    assert calls == [1], "应把需要 OCR 的页批量提交一次"
    assert len(parsed.sections) == 1
    assert parsed.sections[0].page_number == 1
    assert "Grid world example" in parsed.sections[0].markdown
    assert "Grid world example" in parsed.markdown


def test_ocr_pages_and_text_pages_are_merged_in_page_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """混合文档的 section 必须按页码排序，不能把 OCR 结果全排到后面。"""
    fake, _ = _fake_ocr({0: "OCR page one", 1: "OCR page three"})
    monkeypatch.setattr(ocr, "recognize", fake)

    pdf = pymupdf.open()
    _scanned_page(pdf)
    _text_page(pdf, "Native page two")
    _scanned_page(pdf)
    parsed = DocumentParser().parse(_document(pdf))

    assert [section.page_number for section in parsed.sections] == [1, 2, 3]
    assert "OCR page one" in parsed.sections[0].markdown
    assert "Native page two" in parsed.sections[1].markdown
    assert "OCR page three" in parsed.sections[2].markdown


def test_missing_ocr_capability_degrades_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有识别能力的机器仍要能导入文档，只是这些页没有内容。

    这与 `DocumentFormat.availability` 是同一条约定：能力缺失降级，不要让整个导入失败。
    """

    def unavailable(images):
        raise OcrUnavailable("当前系统没有可用的图片文字识别能力")

    monkeypatch.setattr(ocr, "recognize", unavailable)

    pdf = pymupdf.open()
    _scanned_page(pdf)
    _text_page(pdf, "Native page two")
    parsed = DocumentParser().parse(_document(pdf))

    assert [section.page_number for section in parsed.sections] == [2]
    assert parsed.markdown.strip() == "Native page two"


def test_no_ocr_call_when_every_page_has_a_usable_text_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """原生文字页不该触发 OCR —— 那是白花钱：识别只会把好文字换成差文字。"""
    fake, calls = _fake_ocr({})
    monkeypatch.setattr(ocr, "recognize", fake)

    pdf = pymupdf.open()
    _text_page(pdf, "First page body")
    _text_page(pdf, "Second page body")
    parsed = DocumentParser().parse(_document(pdf))

    assert calls == []
    assert len(parsed.sections) == 2


def test_figure_text_carried_by_vector_drawing_reaches_the_section() -> None:
    """矢量图形的文字层必须进 section —— 这是零成本的语义来源。

    一张用 matplotlib 存出来的折线图，`get_images()` 返回 0，轴名与图注全在文字层里。
    不并进来，问「那张图讲了什么」就没有可命中的对象。
    """
    pdf = pymupdf.open()
    page = pdf.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((60, 60), "Body paragraph about the experiment", fontsize=10)
    shape = page.new_shape()
    shape.draw_polyline([(80, 600), (160, 500), (240, 520), (320, 400)])
    shape.finish(color=(0.1, 0.3, 0.8), width=1.5)
    shape.commit()
    page.insert_text((100, 660), "Figure 3 Voltage decay over time", fontsize=9)

    parsed = DocumentParser().parse(_document(pdf))
    markdown = parsed.sections[0].markdown
    assert "Body paragraph" in markdown
    assert "Voltage" in markdown


def test_overlapping_body_inside_the_figure_is_not_indexed_twice() -> None:
    """图的文字层里若含正文，不许把正文再索引一遍。

    实测 05-Markov-Source p21：图的文字层几乎就是正文那一行（只差前两个字符）。
    整段并入会让同一内容出现两次，检索时返回看起来完全相同的引用。
    """
    pdf = pymupdf.open()
    page = pdf.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    body = "Calculation of Entropy Rate for the Markov source model"
    # 正文紧贴绘图区上沿，会被算进图的文字范围 —— 正是实测里那种重叠。
    page.insert_text((100, 490), body, fontsize=9)
    shape = page.new_shape()
    shape.draw_polyline([(80, 600), (160, 500), (240, 520), (320, 400)])
    shape.finish(color=(0.1, 0.3, 0.8), width=1.5)
    shape.commit()

    parsed = DocumentParser().parse(_document(pdf))
    assert parsed.sections[0].markdown.count("Calculation of Entropy Rate") == 1


def test_figure_label_adding_new_terms_is_merged_in() -> None:
    """图内标注带来的**新**术语必须并入 —— 那是图唯一的可检索语义。

    只去重不合并会把图的价值一起丢掉：轴名、单位、图例通常不出现在正文里。
    """
    pdf = pymupdf.open()
    page = pdf.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((60, 60), "Body paragraph about the experiment", fontsize=10)
    shape = page.new_shape()
    shape.draw_polyline([(80, 600), (160, 500), (240, 520), (320, 400)])
    shape.finish(color=(0.1, 0.3, 0.8), width=1.5)
    shape.commit()
    # 标注压在绘图区上沿：既被算作图内文字，又带来正文没有的术语。
    page.insert_text((100, 510), "Voltage decay over time in millivolts", fontsize=9)

    parsed = DocumentParser().parse(_document(pdf))
    markdown = parsed.sections[0].markdown
    assert "Body paragraph" in markdown
    assert "Voltage" in markdown
    assert "millivolts" in markdown
