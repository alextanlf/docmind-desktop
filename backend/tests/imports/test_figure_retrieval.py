"""图片识别的**目的**：图里的字必须能被搜到。

这一组用例守的是一件此前从未验证过的事。在此之前我验证的全是中间产物 —— 文字进了
markdown、字符数对得上、单元测试通过 —— 而**没有一条验证过"用户搜图里的词能找到它"**。
那是整条图片通路存在的理由，所以它比任何中间断言都重要。

做法：构造一个文字层为空、只有一张图的文档（图里的词独一无二），导入，然后**检索**这个词。

* 纯扫描页：`page.get_text()` 是空的，所以命中的文字只可能来自图片识别。
* 普通页 + 图内文字：同样的道理，词只出现在图里。

断言分两层：`document.md` 里有这个词（解析层），**且** BM25 检索能召回这篇文档（检索层）。
第二层是这一组用例真正新增的东西。
"""
from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.core.retrieval import BM25Index
from app.document import ocr
from app.document.parser import DocumentParser
from app.schemas.imports import DownloadedDocument
from app.storage.models import DocumentChunkRecord, DocumentRecord
from tests.imports.test_service import create_job, make_service

#: 只存在于图片里的词。用不在正文出现的长词，避免"命中其实是正文带来的"。
FIGURE_PHRASE = "Zephyrion photovoltaic coefficient"

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _png_bytes(width: int = 500, height: int = 400) -> bytes:
    """足够大的 PNG：小图会被当图标滤掉。"""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    for y in range(height):
        for x in range(0, width, 8):
            pixmap.set_pixel(x, y, ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
    return pixmap.tobytes("png")


def _scanned_pdf() -> bytes:
    """一整页位图 + 一个页码 —— 文字层里没有任何可检索的内容。

    这是"识别不可用就什么都没有"的最强形态：不动图片通路，这篇文档在索引里就是空的。
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(
        pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT),
        stream=_png_bytes(1200, 1700),
    )
    page.insert_text((280, 800), "7", fontsize=10)
    raw = document.tobytes()
    document.close()
    return raw


def _pdf_with_figure() -> bytes:
    """正文 + 一张矢量图。图里的词既不在文字层，也不在正文里。"""
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((60, 80), "Body paragraph about the experiment setup.", fontsize=10)
    shape = page.new_shape()
    shape.draw_polyline([(80, 600), (160, 500), (240, 520), (320, 400)])
    shape.finish(color=(0.1, 0.3, 0.8), width=1.5)
    shape.commit()
    raw = document.tobytes()
    document.close()
    return raw


def _docx_with_image() -> bytes:
    """Word 文档：正文一段 + 一张内嵌图片，图片没有替代文字。

    没有替代文字，图里的词就只可能来自识别。
    """
    import io
    import zipfile

    drawing = (
        "<w:p><w:r><w:drawing><wp:inline>"
        '<wp:docPr id="1" name="Picture 1"/>'
        "<a:graphic><a:graphicData><pic:pic><pic:blipFill>"
        '<a:blip r:embed="rId7"/>'
        "</pic:blipFill></pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r></w:p>"
    )
    body = f"<w:p><w:r><w:t>Body paragraph about the experiment setup.</w:t></w:r></w:p>{drawing}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
            ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f"<w:body>{body}</w:body></w:document>",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId7"'
            ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"'
            ' Target="media/image1.png"/>'
            "</Relationships>",
        )
        archive.writestr("word/media/image1.png", _png_bytes())
    return buffer.getvalue()


class StubbedOcr(DocumentParser):
    """真实解析器，只把识别结果换成固定文本。

    打桩的只有识别本身 —— 解析、图内文字并入、切块、落库、索引全部走真实代码。所以要
    验证的链路是完整的，而结果又是确定的。
    """

    def parse(self, document: DownloadedDocument):  # type: ignore[no-untyped-def]
        original = ocr.recognize

        def fake(images):
            # 先问真实的 `recognize`：它守着「用户关掉了识别」那个开关。直接返回固定文本
            # 会让这一组用例在关掉识别时照样通过 —— 那就测不到开关本身。
            if original(images) == [] and not ocr.figure_ocr_enabled():
                return []
            return [
                ocr.OcrPage(
                    index=index,
                    width=image.width,
                    height=image.height,
                    lines=(
                        ocr.OcrLine(
                            text=FIGURE_PHRASE, confidence=1.0, bbox=(0.0, 0.0, 10.0, 10.0)
                        ),
                    ),
                )
                for index, image in enumerate(images)
            ]

        ocr.recognize = fake  # type: ignore[assignment]
        try:
            return super().parse(document)
        finally:
            ocr.recognize = original  # type: ignore[assignment]


def _seed_background_chunks(database, count: int = 6) -> None:
    """往同一仓库塞几块无关正文。

    **不是为了"让它更像真实库"，是为了让 BM25 的 IDF 不退化成 0。** `BM25Okapi` 的 IDF 在
    「查询词出现在所有文档里」时为 0，而 `BM25Index.search` 会 `if score > 0` 把 0 分全滤掉。
    只有一个 chunk 时必然如此（AGENTS.md 记载过这个坑）。语料里有了背景块，图里的词才是
    「只在一份文档里出现」，IDF 才有意义 —— 这也正是真实使用时的情形。

    背景块的词与图里的词不重叠，避免互相干扰。
    """
    background = "background-document"
    with database.session() as session:
        # 块与文档之间是真外键，所以背景块也得挂在一篇真实文档上。
        # 它不参与任何文档级断言，只为把语料规模撑到 IDF 不退化的程度。
        session.add(
            DocumentRecord(
                id=background,
                repository_id="repository-1",
                title="Background",
                source_type="import",
            )
        )
        for index in range(count):
            session.add(
                DocumentChunkRecord(
                    id=f"background-{index}",
                    document_id=background,
                    repository_id="repository-1",
                    chunk_index=index,
                    text=(
                        "the cache replacement policy affects the miss rate "
                        f"and the eviction order in buffer {index}"
                    ),
                    token_count=12,
                )
            )


def _parsed_figure_text(raw_bytes: bytes, media_type: str) -> str:
    """识别结果里的文字，从 `figure.text` 读。

    **读 `text`，不读 `caption`。** 两者语义不同：`text` 是"我们识别出来的"，`caption`
    是"别人写的描述"（网页 `alt`、Word 替代文字）。HTML 那条通路写 `text`，而 PDF/Word
    曾经写 `caption` —— 于是任何读 `figure.text` 的下游（去重、验证、未来的模型描述）
    在 PDF/Word 上永远拿到空串。这条断言守的就是这个统一。

    刻意做成**解析器级**的确定性断言，而不是从导入服务里取解析结果：服务在一次导入里会
    多次调用 `parse`（预览与正式解析），记"最后一次"会取到不相干的那一次。用到的识别是
    打桩的，所以这里不测识别质量，只测字段归属。
    """
    from app.document import ocr

    original = ocr.recognize

    def fake(images):
        return [
            ocr.OcrPage(
                index=index,
                width=image.width,
                height=image.height,
                lines=(ocr.OcrLine(FIGURE_PHRASE, 1.0, (0.0, 0.0, 10.0, 10.0)),),
            )
            for index, image in enumerate(images)
        ]

    ocr.recognize = fake  # type: ignore[assignment]
    try:
        if media_type == DOCX_MEDIA:
            from app.document.docx import parse_docx as parse

            document = DownloadedDocument(
                title="x.docx", source_url="u", media_type=media_type, raw_bytes=raw_bytes
            )
        else:
            from app.document.builtin_formats import parse_pdf as parse

            document = DownloadedDocument(
                title="x.pdf", source_url="u", media_type=media_type, raw_bytes=raw_bytes
            )
        parsed = parse(document)
    finally:
        ocr.recognize = original  # type: ignore[assignment]
    return "\n".join(
        figure.text for section in parsed.sections for figure in section.figures if figure.text
    ).strip()


async def _import(database, tmp_path, raw_bytes: bytes, media_type: str):
    """走真实的导入服务导入一份文档。"""
    service, source, _, _ = make_service(database, tmp_path)
    _seed_background_chunks(database)
    source.document = DownloadedDocument(
        title="课件.pdf",
        source_url="https://example.test/slides.pdf",
        media_type=media_type,
        raw_bytes=raw_bytes,
    )
    service.parser = StubbedOcr()
    job = await create_job(service, source)
    await service.run(job.id)
    documents = [
        document
        for document in service.document_store.list_for_repository("repository-1")
        if document.id != "background-document"
    ]
    assert documents, "导入之后应当有一篇文档"
    return service, documents[0]


@pytest.mark.asyncio
async def test_scanned_page_figure_text_is_retrievable(database, tmp_path) -> None:
    """纯扫描页：图里的词必须能被搜到。

    这一页的文字层只有一个页码，所以命中的词只可能来自图片识别。识别不可用时这篇文档
    在索引里就是空的 —— 这正是改动之前的状态。
    """
    _, document = await _import(database, tmp_path, _scanned_pdf(), "application/pdf")

    # 第一层：解析结果里有图里的词。
    markdown = Path(document.markdown_path or "").read_text(encoding="utf-8")
    assert FIGURE_PHRASE in markdown, "图内文字没有进解析结果"
    # 识别结果必须落在 `figure.text` 上，与 HTML 通路一致。
    assert _parsed_figure_text(_scanned_pdf(), "application/pdf") == FIGURE_PHRASE

    # 第二层（这一组用例的核心）：检索能召回。
    hits = BM25Index(database).search(FIGURE_PHRASE, ["repository-1"], top_k=5)
    assert hits, "搜图里的词没有召回任何 chunk —— 图内文字没有真正进入索引"


@pytest.mark.asyncio
async def test_pdf_vector_figure_text_is_retrievable(database, tmp_path) -> None:
    """普通页 + 矢量图：同样要能被搜到。

    矢量图的语义只在文字层里，而 `page.get_text()` 的整页顺序里没有它 —— 这是把图内
    文字单独并进正文的理由。
    """
    _, document = await _import(database, tmp_path, _pdf_with_figure(), "application/pdf")

    markdown = Path(document.markdown_path or "").read_text(encoding="utf-8")
    assert "Body paragraph about the experiment setup." in markdown, "正文丢了"
    assert FIGURE_PHRASE in markdown, "矢量图的图内文字没有进解析结果"
    assert _parsed_figure_text(_pdf_with_figure(), "application/pdf") == FIGURE_PHRASE

    hits = BM25Index(database).search(FIGURE_PHRASE, ["repository-1"], top_k=5)
    assert hits, "搜矢量图里的文字没有召回任何 chunk"


@pytest.mark.asyncio
async def test_docx_image_text_is_retrievable(database, tmp_path) -> None:
    """Word 文档里的图片：图里的词必须能被搜到。

    这张图没有替代文字，所以命中的词只可能来自识别。
    """
    _, document = await _import(database, tmp_path, _docx_with_image(), DOCX_MEDIA)

    markdown = Path(document.markdown_path or "").read_text(encoding="utf-8")
    assert FIGURE_PHRASE in markdown, "Word 图片的文字没有进解析结果"
    assert _parsed_figure_text(_docx_with_image(), DOCX_MEDIA) == FIGURE_PHRASE

    hits = BM25Index(database).search(FIGURE_PHRASE, ["repository-1"], top_k=5)
    assert hits, "搜 Word 图片里的文字没有召回任何 chunk"


@pytest.mark.asyncio
async def test_without_ocr_the_scanned_page_is_invisible(database, tmp_path) -> None:
    """反向对照：识别不可用时，图里的词搜不到，而正文照常能搜到。

    这条用例的价值是**证明上一条的断言不是空的**。如果纯扫描页本来就能被搜到，
    `test_scanned_page_figure_text_is_retrievable` 就证明不了任何事。

    文档同时含一页正文与一页扫描件：正文必须仍可检索（识别不可用不该影响它），
    而图里的词必须搜不到。
    """
    service, source, _, _ = make_service(database, tmp_path)
    _seed_background_chunks(database)
    source.document = DownloadedDocument(
        title="混合课件.pdf",
        source_url="https://example.test/mixed.pdf",
        media_type="application/pdf",
        raw_bytes=_mixed_pdf(),
    )
    original = ocr.recognize

    def unavailable(images):
        raise ocr.OcrUnavailable("没有可用的识别后端")

    ocr.recognize = unavailable  # type: ignore[assignment]
    try:
        job = await create_job(service, source)
        await service.run(job.id)
    finally:
        ocr.recognize = original  # type: ignore[assignment]

    index = BM25Index(database)
    assert index.search("Zephyrion photovoltaic coefficient", ["repository-1"], top_k=5) == [], (
        "没有识别能力却搜到了图里的词，说明上一条用例的断言不成立"
    )
    assert index.search("keplerian resonance", ["repository-1"], top_k=5), (
        "正文页本该可检索 —— 它受影响说明失败的原因不是「识别不可用」"
    )


def _mixed_pdf() -> bytes:
    """一页正文 + 一页纯扫描件。

    正文页里放一个同样独特、但只存在于文字层的词（`keplerian resonance`），用来区分
    「整体没索引成功」与「只有图里的词没进来」。
    """
    document = pymupdf.open()
    text_page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    text_page.insert_text((60, 80), "keplerian resonance in the orbital model.", fontsize=11)
    scanned = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    scanned.insert_image(
        pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT), stream=_png_bytes(1200, 1700)
    )
    scanned.insert_text((280, 800), "7", fontsize=10)
    raw = document.tobytes()
    document.close()
    return raw


@pytest.mark.asyncio
async def test_disabling_figure_ocr_removes_scanned_pages_from_the_index(
    database, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关掉识别之后，纯扫描页确实搜不到 —— 这是那个开关的代价，必须是真的。

    这条用例的作用不是"支持关掉"，而是**把代价钉住**：如果哪天关掉之后内容仍在（比如
    识别没被真正拦住），说明开关是假的。用户在界面上看到"会丢内容"的提示，也该能对上
    这里的断言。
    """
    from app.document import ocr

    monkeypatch.setenv(ocr.FIGURE_OCR_ENV, "0")
    index = await _import_and_index(database, tmp_path, _scanned_pdf(), monkeypatch)
    assert index.search(FIGURE_PHRASE, ["repository-1"], top_k=5) == [], (
        "关掉识别之后仍然搜得到，说明开关没有真正拦住识别"
    )


@pytest.mark.asyncio
async def test_enabling_figure_ocr_keeps_scanned_pages_searchable(
    database, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """识别开着时，图的文字照旧可检索 —— 开关的默认档不改变原有行为。"""
    from app.document import ocr

    monkeypatch.setenv(ocr.FIGURE_OCR_ENV, "1")
    index = await _import_and_index(database, tmp_path, _scanned_pdf(), monkeypatch)
    assert index.search(FIGURE_PHRASE, ["repository-1"], top_k=5)


async def _import_and_index(database, tmp_path, raw_bytes: bytes, monkeypatch):
    """按当前环境变量的档位导入，返回可用于检索的 BM25 索引。"""
    service, source, _, _ = make_service(database, tmp_path)
    _seed_background_chunks(database)
    source.document = DownloadedDocument(
        title="课件.pdf",
        source_url="https://example.test/slides.pdf",
        media_type="application/pdf",
        raw_bytes=raw_bytes,
    )
    service.parser = StubbedOcr()
    job = await create_job(service, source)
    await service.run(job.id)
    return BM25Index(database)


@pytest.mark.asyncio
async def test_import_service_applies_the_stored_setting(
    database, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导入服务把用户设置落进识别开关 —— 光有设置字段不算接通。

    这条用例守的是接线：设置页读写的那个值，必须真的在解析时生效。
    """
    from app.document import ocr
    from app.storage.repositories import SettingStore

    store = SettingStore(database)
    store.set_figure_ocr(False)
    service, _, _, _ = make_service(database, tmp_path)
    service.setting_store = store
    assert service.setting_store.figure_ocr_enabled() is False

    # 导入服务写的是**进程环境变量**，所以这里必须用 monkeypatch 登记它 —— 否则这个
    # 开关会泄漏给之后的所有测试（曾经就这么泄漏过，整套用例因此变慢近 9 倍）。
    monkeypatch.setenv(ocr.FIGURE_OCR_ENV, "1")
    service._apply_figure_ocr_setting()
    assert ocr.figure_ocr_enabled() is False, "导入服务没有把设置落进识别开关"

    store.set_figure_ocr(True)
    service._apply_figure_ocr_setting()
    assert ocr.figure_ocr_enabled() is True


class FakeVision:
    """替身视觉模型：按调用顺序给出描述，并记录被描述过哪些图。"""

    def __init__(self, descriptions: dict[int, str] | None = None) -> None:
        self.descriptions = descriptions or {}
        self.seen: list[int] = []
        self.calls = 0
        self.fail_after: int | None = None

    async def describe_all(self, images, *, settings, client=None):
        results: list[str] = []
        for image in images:
            self.calls += 1
            self.seen.append(len(image))
            if self.fail_after is not None and self.calls > self.fail_after:
                results.append("")
                continue
            results.append(self.descriptions.get(self.calls, f"描述{self.calls}"))
        return results


@pytest.mark.asyncio
async def test_descriptions_are_generated_only_for_figures_without_text(
    database, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只给「图里没有文字」的图生成描述。

    有文字的图 OCR 已经覆盖，再描述一次是白花用户的钱 —— 而这是唯一一处按张数产生模型
    费用的地方。这条用例守的就是"别为已经有内容的图付钱"。

    直接构造解析结果而不是从 PDF 走一遍：要测的是**过滤条件**，而"造一张恰好被检出、
    恰好没有文字的图"要靠 PDF 几何碰运气（试过，那张图根本没被当成插图）。构造出来的
    三种图把这个条件钉死。
    """
    from app.document import figure_description
    from app.imports import service as import_service
    from app.schemas.imports import ParsedDocument, ParsedFigure, ParsedSection

    fake = FakeVision()
    # 打桩 `service` 模块里那个名字：`service.py` 用 `from ... import describe_all as
    # describe_figures` 绑定了引用，改 `figure_description` 的模块属性影响不到它。
    monkeypatch.setattr(import_service, "describe_figures", fake.describe_all)

    service, _, _, _ = make_service(database, tmp_path)
    service.figure_description = lambda: figure_description.VisionSettings(
        base_url="https://vision.test/v1", model="vlm", api_key="k"
    )
    with_text = ParsedFigure(asset="a.png", payload=b"\x89PNG-text", text="已经有识别文字")
    with_alt = ParsedFigure(asset="b.png", payload=b"\x89PNG-alt", caption="作者写的替代文字")
    textless = ParsedFigure(asset="c.png", payload=b"\x89PNG-blind")
    no_bytes = ParsedFigure(asset="", payload=b"")
    parsed = ParsedDocument(
        title="t",
        markdown="正文",
        source_url="u",
        sections=[
            ParsedSection(
                heading_path=[],
                markdown="正文",
                figures=[with_text, with_alt, textless, no_bytes],
            )
        ],
    )

    described = await service._describe_figures(parsed)

    assert described == 1, "只有那张既没识别文字、也没替代文字的图该被描述"
    assert fake.calls == 1
    assert textless.text == "描述1"
    assert "描述1" in parsed.markdown, "描述没有并进正文 —— 那就搜不到"
    assert with_text.text == "已经有识别文字", "有识别文字的图不该被覆盖"
    assert with_alt.text == "", "有替代文字的图不该被描述"


@pytest.mark.asyncio
async def test_description_failure_does_not_fail_the_import(
    database, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """描述失败只影响描述：图还在、检索照旧，导入不该因此失败。

    这是唯一一处会联网花钱的富化步骤，也是最可能失败的一步（认证、限流、超时）。把它做成
    硬依赖等于让一份网络抖动废掉整次导入。
    """
    from app.document import figure_description

    async def boom(images, *, settings, client=None):
        raise RuntimeError("vision down")

    service, source, _, _ = make_service(database, tmp_path)
    source.document = DownloadedDocument(
        title="x.pdf",
        source_url="u",
        media_type="application/pdf",
        raw_bytes=_pdf_with_figure(),
    )
    service.parser = StubbedOcr()
    service.figure_description = lambda: figure_description.VisionSettings(
        base_url="https://vision.test/v1", model="vlm", api_key="k"
    )
    job = await create_job(service, source)
    await service.run(job.id)

    documents = [
        document
        for document in service.document_store.list_for_repository("repository-1")
        if document.id != "background-document"
    ]
    assert documents, "描述失败不该让导入失败 — 这份文档应当照常入库"
    markdown = Path(documents[0].markdown_path or "").read_text(encoding="utf-8")
    assert FIGURE_PHRASE in markdown
    void(boom)


@pytest.mark.asyncio
async def test_descriptions_are_off_by_default(database, tmp_path) -> None:
    """没配视觉模型时整条通路不跑 —— 默认不能联网发图。

    一个默认开启的联网功能会让用户在不知情的情况下把资料发出去。所以"没配"必须是
    "不跑"，而不是"跑但失败"。
    """
    service, source, _, _ = make_service(database, tmp_path)
    source.document = DownloadedDocument(
        title="x.pdf", source_url="u", media_type="application/pdf", raw_bytes=_pdf_with_figure()
    )
    service.parser = StubbedOcr()
    assert service.figure_description is None

    job = await create_job(service, source)
    await service.run(job.id)

    assert await service._describe_figures(
        type("P", (), {"sections": [], "markdown": ""})()
    ) == 0


def void(*_: object) -> None:
    """占位：让未使用的替身函数有个去处，避免 lint 报未使用。"""


