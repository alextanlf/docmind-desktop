"""网页图片：抽取、过滤、抓取、识别、归属。

这一组用例最重要的不是「能抽到图」，而是**挡得住什么**：网页里可以写
``<img src="http://127.0.0.1:18900/api/...">``，而抓取是导入流程里唯一会主动访问第三方
地址的一步。SSRF 由 `SafeHttpClient` 负责（它的测试在别处），这里守的是调用方确实走了
那条路、以及单张失败不会拖垮整篇文档。
"""
from __future__ import annotations

import pymupdf
import pytest
from bs4 import BeautifulSoup

from app.document.html_figures import (
    MAX_IMAGES_PER_PAGE,
    extract_image_refs,
    fetch_pending,
    image_media_type,
    is_fetchable,
)
from app.document.safe_http import HttpSnapshot
from app.schemas.imports import ParsedDocument, ParsedFigure, ParsedSection

PAGE_URL = "https://example.test/guide"


def _png_bytes(width: int = 400, height: int = 300) -> bytes:
    """足够大的 PNG：小图会被 `MIN_IMAGE_BYTES` 当图标滤掉。"""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    for y in range(height):
        for x in range(0, width, 8):
            pixmap.set_pixel(x, y, ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
    return pixmap.tobytes("png")


class FakeClient:
    """替身 `SafeHttpClient`：只按 URL 返回预置字节，并记录被请求过哪些地址。"""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    async def get(self, url: str, *, max_bytes: int, **_: object) -> HttpSnapshot:
        self.requested.append(url)
        payload = self.responses[url]
        return HttpSnapshot(
            url=url,
            status_code=200,
            media_type=image_media_type(payload) or "application/octet-stream",
            headers={},
            body=payload,
        )


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _parsed(markdown: str, figures: list[ParsedFigure]) -> ParsedDocument:
    section = ParsedSection(heading_path=[], markdown=markdown, figures=figures)
    return ParsedDocument(title="guide", markdown=markdown, source_url=PAGE_URL, sections=[section])


# -- 抽取 ---------------------------------------------------------------------


def test_srcset_prefers_the_largest_candidate() -> None:
    """`srcset` 要挑分辨率最高的那个。

    第一个候选常常是给窄屏用的缩略图，里面的文字小到识别不出来 —— 挑错了等于白抓一次还
    拿不到内容。
    """
    refs = extract_image_refs(
        _soup('<img src="small.png" srcset="small.png 320w, big.png 1600w, mid.png 800w">'),
        PAGE_URL,
    )
    assert [ref.url for ref in refs] == ["https://example.test/big.png"]


def test_lazy_loading_attributes_are_recognised() -> None:
    """懒加载的图 `src` 常常是占位图，真地址在 `data-src` 上。"""
    refs = extract_image_refs(
        _soup('<img src="placeholder.gif" data-src="/real/figure.png">'), PAGE_URL
    )
    assert [ref.url for ref in refs] == ["https://example.test/real/figure.png"]


def test_relative_and_protocol_relative_urls_are_resolved() -> None:
    """真实网页两种都大量出现：`/static/...` 与 `//cdn.example/...`。"""
    refs = extract_image_refs(
        _soup('<img src="/a.png"><img src="//cdn.other.test/b.png">'), PAGE_URL
    )
    assert sorted(ref.url for ref in refs) == [
        "https://cdn.other.test/b.png",
        "https://example.test/a.png",
    ]


def test_data_uri_is_skipped() -> None:
    """内联图不走网络，不能把 base64 当 URL 去请求。"""
    refs = extract_image_refs(_soup('<img src="data:image/png;base64,iVBORw0KGgo=">'), PAGE_URL)
    assert refs == []


def test_duplicate_urls_are_counted_once() -> None:
    refs = extract_image_refs(
        _soup('<img src="/same.png"><img src="/same.png"><img src="/other.png">'), PAGE_URL
    )
    assert len(refs) == 2
    same = next(ref for ref in refs if ref.url.endswith("/same.png"))
    assert same.references == 2


def test_alt_text_is_carried() -> None:
    """替代文字是零成本的人工描述。"""
    refs = extract_image_refs(_soup('<img src="/a.png" alt="系统结构图">'), PAGE_URL)
    assert refs[0].alt == "系统结构图"


# -- 过滤 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/logo.svg",
        "https://example.test/static/images/icons/enwiki.svg",
        "https://example.test/assets/sprite.png",
        "https://example.test/img/favicon.ico.png",
    ],
)
def test_decorative_and_vector_images_are_skipped(url: str) -> None:
    """SVG 与图标类路径不值得发请求：识别不了，也不是内容。"""
    fetchable, reason = is_fetchable(url)
    assert fetchable is False
    assert reason


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:18900/api/internal",
        "http://localhost/a.png",
        "http://foo.localhost/a.png",
        "http://10.0.0.5/a.png",
        "http://192.168.1.1/a.png",
        "http://[::1]/a.png",
    ],
)
def test_local_and_private_addresses_are_rejected(url: str) -> None:
    """指向本机/内网的地址必须在最便宜的那一层就挡掉。

    真正的 SSRF 防线是 `SafeHttpClient._check_host`（它连**解析到私网的域名**都拦，
    那是 DNS rebinding 的正确防法），但那是要发起请求时才生效的一层。一个明显指向本机的
    URL 没有理由走到那里 —— 这是纵深防御。

    实测这条路径曾经放行过 `http://127.0.0.1:18900/api/internal`：`SourceValidator`
    只拦字面 IP 且只在下载器里用，图片这条路根本没过它。
    """
    fetchable, reason = is_fetchable(url)
    assert fetchable is False
    assert "内网" in reason or "本机" in reason


def test_content_image_paths_are_fetchable() -> None:
    fetchable, reason = is_fetchable("https://cdn.test/commons/thumb/e/e1/diagram.png")
    assert fetchable is True
    assert reason == ""


# -- 格式识别 -----------------------------------------------------------------


def test_media_type_comes_from_the_bytes_not_the_server() -> None:
    """服务器给的 `Content-Type` 不可信，按内容判断。"""
    assert image_media_type(_png_bytes(8, 8)) == "image/png"
    assert image_media_type(b"<html>not an image</html>") is None


# -- 抓取与识别 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_downloads_and_recognizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """下载 → 识别 → 文字进正文 → 挂到图上。

    这里打桩识别，测的是**链路**：字节有没有到位、资产名有没有算出来、文字有没有并进
    正文。识别质量由 `test_ocr.py` 的真机用例覆盖。
    """
    from app.document import ocr

    url = "https://example.test/figure.png"
    figure = ParsedFigure(asset="", source_url=url)
    parsed = _parsed("正文段落", [figure])

    def fake_recognize(images):
        return [
            ocr.OcrPage(
                index=0,
                width=images[0].width,
                height=images[0].height,
                lines=(ocr.OcrLine("Cache replacement policy", 1.0, (0.0, 0.0, 10.0, 10.0)),),
            )
        ]

    monkeypatch.setattr(ocr, "recognize", fake_recognize)
    client = FakeClient({url: _png_bytes()})

    fetched = await fetch_pending(parsed, client=client)  # type: ignore[arg-type]

    assert fetched == 1
    assert client.requested == [url]
    assert figure.payload.startswith(b"\x89PNG")
    assert figure.asset.endswith(".png")
    assert "Cache replacement policy" in parsed.sections[0].markdown
    assert figure in parsed.sections[0].figures


@pytest.mark.asyncio
async def test_tiny_downloaded_image_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """下载到的小图（间隔条、装饰点）不该留下。"""
    url = "https://example.test/tiny.png"
    figure = ParsedFigure(asset="", source_url=url)
    parsed = _parsed("正文", [figure])
    tiny = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 6, 6)).tobytes("png")

    fetched = await fetch_pending(parsed, client=FakeClient({url: tiny}))  # type: ignore[arg-type]

    assert fetched == 0
    assert figure.payload == b""
    assert figure.failure == "图片过小"


@pytest.mark.asyncio
async def test_non_image_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务器返回 HTML 错误页时不能把它当图存下来。"""
    url = "https://example.test/notfound.png"
    figure = ParsedFigure(asset="", source_url=url)
    parsed = _parsed("正文", [figure])

    fetched = await fetch_pending(
        parsed, client=FakeClient({url: b"<html>404</html>" * 400})  # type: ignore[arg-type]
    )

    assert fetched == 0
    assert figure.failure == "不是可识别的图片格式"


@pytest.mark.asyncio
async def test_failure_of_one_image_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一个页面上坏掉几张图不该让整篇文档导入失败，也不该拖累其余的图。"""
    from app.document import ocr

    good = "https://example.test/good.png"
    bad = "https://example.test/bad.png"
    figures = [
        ParsedFigure(asset="", source_url=bad),
        ParsedFigure(asset="", source_url=good),
    ]
    parsed = _parsed("正文", figures)

    monkeypatch.setattr(ocr, "recognize", lambda images: [])
    fetched = await fetch_pending(  # type: ignore[arg-type]
        parsed, client=FakeClient({good: _png_bytes(), bad: b"not an image"})
    )

    assert fetched == 1
    assert figures[1].payload.startswith(b"\x89PNG")
    assert figures[0].failure


@pytest.mark.asyncio
async def test_image_count_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """塞满图的页面不能把导入拖成几分钟。"""
    from app.document import ocr

    monkeypatch.setattr(ocr, "recognize", lambda images: [])
    urls = [f"https://example.test/{index}.png" for index in range(MAX_IMAGES_PER_PAGE + 5)]
    parsed = _parsed("正文", [ParsedFigure(asset="", source_url=url) for url in urls])
    client = FakeClient({url: _png_bytes(60, 40) for url in urls})

    await fetch_pending(parsed, client=client)  # type: ignore[arg-type]

    assert len(client.requested) == MAX_IMAGES_PER_PAGE


@pytest.mark.asyncio
async def test_figures_without_source_url_are_ignored() -> None:
    """PDF/Word 的图没有 source_url，不该被这条通路碰。"""
    parsed = _parsed("正文", [ParsedFigure(asset="abc.png", payload=b"\x89PNG")])
    client = FakeClient({})

    assert await fetch_pending(parsed, client=client) == 0  # type: ignore[arg-type]
    assert client.requested == []


@pytest.mark.parametrize("width", [16, 24, 48, 99])
def test_small_declared_width_is_rejected_before_downloading(width: int) -> None:
    """页面上声明了宽度的图，小到那个程度就不必下载。

    这是从**标记**上就能判断的一半：`<img width="16">` 是图标，不必联网去取。与
    `MIN_IMAGE_BYTES` 互补 —— 后者要下载之后才量得到，而下载有代价（网络，以及让第三方
    主机看到你的地址）。
    """
    from app.document.html_figures import MIN_DECLARED_WIDTH, is_too_small_declared

    assert is_too_small_declared(width) is True
    assert is_too_small_declared(MIN_DECLARED_WIDTH) is False


def test_undeclared_width_is_not_rejected() -> None:
    """没声明宽度的图不能因此被拒 —— 绝大多数内容图都不写 width 属性。"""
    from app.document.html_figures import is_too_small_declared

    assert is_too_small_declared(0) is False


def test_declared_width_comes_from_the_attribute_not_srcset() -> None:
    """`srcset` 的尺寸描述符与显示尺寸无关，不能拿来判断图标。"""
    refs = extract_image_refs(
        _soup('<img src="/a.png" srcset="/a.png 16w, /a-big.png 1200w">'), PAGE_URL
    )
    # 挑了最大候选，而 width 为 0（没有 width 属性）→ 不该被当成小图。
    assert refs[0].url.endswith("/a-big.png")
    assert refs[0].width == 0


@pytest.mark.asyncio
async def test_figure_text_lands_in_one_section_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """图内文字只进它所属的那一节，绝不复制到每一节。

    这条用例来自一次真实网页验证：一个维基百科页面有 40 节、8 张图，抽取阶段把图都挂在
    第一节，而并入发生在归属之前 —— 于是同一段 MS-DOS 截图文字被并进 **9 个不同的节**，
    正文从 120017 字符虚涨到 159003。当时的单元测试只有**一节**，所以完全看不到它。

    检索侧的后果比字符数更严重：同一个内容会在 9 个位置被返回。
    """
    from app.document import html_figures, ocr

    url = "https://example.test/figure.png"
    # 两节的正文都**明确提到**识别结果里的词，这样归属是确定的（A 节全部命中），
    # 用例检验的就只剩"有没有被复制到别处"这一件事。
    section_a = ParsedSection(
        heading_path=["Alpha"],
        markdown="the Zephyrion photovoltaic coefficient appears here",
        figures=[ParsedFigure(asset="", source_url=url)],
    )
    section_b = ParsedSection(
        heading_path=["Beta"],
        markdown="elsewhere we discuss the Zephyrion effect on cells",
        figures=[],
    )
    # `markdown` 按真实 `parse_html` 的做法填成各节之和 —— 留空的话"文档级不重复"
    # 这条断言就成了空断言（0 == 1 恒假），看不见任何东西。
    parsed = ParsedDocument(
        title="multi",
        markdown=f"{section_a.markdown}\n\n{section_b.markdown}",
        source_url=PAGE_URL,
        sections=[section_a, section_b],
    )

    monkeypatch.setattr(
        ocr,
        "recognize",
        lambda images: [
            ocr.OcrPage(
                index=0,
                width=images[0].width,
                height=images[0].height,
                lines=(ocr.OcrLine("Zephyrion photovoltaic coefficient", 1.0, (0, 0, 10, 10)),),
            )
        ],
    )
    figure = section_a.figures[0]
    await html_figures.fetch_pending(parsed, client=FakeClient({url: _png_bytes()}))  # type: ignore[arg-type]

    # 识别结果落在 `text` 上：`caption` 是页面给的替代文字（`alt`），语义不同。
    assert figure.text == "Zephyrion photovoltaic coefficient"
    # 说"图内文字并入正文"时，正文指的是**它那一节**。两节的正文各自提到过
    # "Zephyrion"（那是为了固定归属而写的），所以只有**整段识别结果**才是不变量。
    phrase = "Zephyrion photovoltaic coefficient"
    assert phrase in section_a.markdown
    assert phrase not in section_b.markdown, "图内文字被复制到了别的节"
    assert parsed.markdown.count(phrase) == 1, "同一段图内文字在文档级出现了不止一次"
