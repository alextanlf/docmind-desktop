"""The document formats DocMind ships with.

Every built-in format is declared once here — its suffix, its media type, how
to parse it, and how to recognise its payload. The staging store, the directory
scanner, the downloader and the parser all read these declarations instead of
carrying their own copies, which is what makes adding a format a one-file change
and lets a plugin add one without touching any of them.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import pymupdf
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from app.api.errors import DomainError
from app.document import html_figures, ocr, page_kind
from app.document.docx import parse_docx
from app.document.extraction import (
    extract_main_content,
    strip_document_noise,
    strip_hidden_content,
)
from app.document.figures import (
    FigureRegion,
    detect_figures,
    is_marker,
    marker_digests,
    sanitize_text,
)
from app.document.formats import DocumentFormat, FormatRegistry
from app.document.markdown_sections import sections_from_markdown, title_from_markdown
from app.schemas.imports import (
    DownloadedDocument,
    ParsedDocument,
    ParsedFigure,
    ParsedSection,
)

PDF_MEDIA_TYPE = "application/pdf"
MARKDOWN_MEDIA_TYPE = "text/markdown"
HTML_MEDIA_TYPE = "text/html"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"


def parse_markdown(document: DownloadedDocument) -> ParsedDocument:
    try:
        markdown = document.raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DomainError(
            "SOURCE_UNSUPPORTED", "Markdown 必须使用 UTF-8 编码", 400, False
        ) from None
    title = title_from_markdown(markdown) or document.title
    return _from_markdown(document, markdown, title)


def parse_html(document: DownloadedDocument) -> ParsedDocument:
    soup = BeautifulSoup(document.raw_bytes, "html.parser")
    strip_hidden_content(soup)
    title_node = soup.find("h1") or soup.title
    title = title_node.get_text(" ", strip=True) if title_node else document.title
    container = extract_main_content(soup)
    if container is not None:
        strip_document_noise(container)
    if soup.head:
        soup.head.decompose()
    target = container if container is not None else soup
    markdown = markdownify(
        str(target), heading_style="ATX", code_language_callback=_code_language
    ).strip()
    languages = [_code_language(code) for code in target.select("pre > code")]
    language_index = 0

    def add_code_language(match: re.Match[str]) -> str:
        nonlocal language_index
        language = languages[language_index] if language_index < len(languages) else None
        language_index += 1
        return f"```{language or ''}{match.group(1)}```"

    markdown = re.sub(r"```(\n.*?\n)```", add_code_language, markdown, flags=re.DOTALL)
    parsed = _from_markdown(document, markdown, title or document.title)
    _attach_pending_images(parsed, target, document.source_url)
    return parsed


def _attach_pending_images(
    parsed: ParsedDocument, container: Tag, base_url: str
) -> None:
    """把网页里引用的图片记成「待下载」的插图。

    这里**不下载**（同步解析器不做网络访问）：只把地址记在 ``ParsedFigure.source_url``
    上，由导入服务在解析之后补字节与文字。判据（SVG、图标路径、显式小尺寸）在这一步就
    应用，免得为注定要丢的图去发起请求。
    """
    # 目录里的本地 .html 没有可解析相对地址的基址：那种情况下 `urljoin` 会让相对地址
    # 落回空串，于是只认绝对 URL —— 这正是想要的行为。
    candidates = [
        ref
        for ref in html_figures.extract_image_refs(_as_soup(container), base_url)
        if html_figures.is_fetchable(ref.url)[0]
        # 声明了宽度的按宽度挡掉 —— 这是不必下载就能做的判断，而下载要访问第三方主机。
        and not html_figures.is_too_small_declared(ref.width)
    ]
    if not candidates or not parsed.sections:
        return
    figures = [
        ParsedFigure(
            asset="",
            caption=ref.alt,
            source_url=ref.url,
        )
        for ref in candidates
    ]
    # 先都挂到第一节：网页没有 PDF 那样的页码，归属要靠图内文字，而那要等下载与识别
    # 之后才能定 —— 那时由 `html_figures.attach_fetched` 重新分配。
    parsed.sections[0].figures.extend(figures)


def _as_soup(container: Tag) -> BeautifulSoup:
    """把选中的容器包回一个 BeautifulSoup。

    `extract_main_content` 返回的是已经挑好的 DOM 子树，而 `extract_image_refs` 需要
    BeautifulSoup 的查找接口 —— 转一次字符串比让抽取器同时接受两种输入更简单。
    """
    return BeautifulSoup(str(container), "html.parser")


def parse_pdf(document: DownloadedDocument) -> ParsedDocument:
    try:
        pdf = pymupdf.open(stream=document.raw_bytes, filetype="pdf")
    except (RuntimeError, ValueError):
        raise DomainError("SOURCE_UNSUPPORTED", "PDF 文件无效", 400, False) from None
    try:
        sections = _pdf_sections(pdf)
    finally:
        pdf.close()
    markdown = "\n\n".join(section.markdown for section in sections)
    return ParsedDocument(
        title=document.title,
        markdown=markdown,
        source_url=document.source_url,
        sections=sections,
    )


def _pdf_sections(pdf: pymupdf.Document) -> list[ParsedSection]:
    """逐页产出 section，按页型选择取文方式。

    旧实现只有一条路：``page.get_text("text")``，没有文字就整页丢弃。实测语料里这条假设
    对三类页都不成立（分布见 ``docs/figure-distribution.json`` 与
    ``docs/textbook-figure-distribution.json``）：

    * **整页截图型幻灯片**：文字层只剩页码。旧实现把它们全丢掉 ——
      ``reinforcement-learning.pdf`` 64 页里只有 8 页留下了内容。
    * **纯扫描书**：正文与插图在同一张整页位图里，文字层 0 字符或只有水印。
    * **扫描 + OCR 的书**：文字层是真的，**不能**再 OCR 一遍（会把好文字换成差文字）。

    所以先判页型，再决定取文字层还是走 OCR；两件事都不做才是真正的丢内容。

    OCR 一次收集、按批并发调用（实测 474 页的书 290.7s → 177.1s）。
    """
    sections: list[ParsedSection] = []
    pending: list[tuple[int, ocr.RenderedImage, list[ParsedFigure]]] = []
    #: 原生页的 (页码, 正文, **未渲染的**图形区域)。正文先留在手里：插图里的字识别出来
    #: 之后要并进它，而识别发生在整份文档遍历完之后（要批量调用）。图形区域也先不渲染 ——
    #: 模板标记要按整份文档统计才能认出来，先渲染等于给注定要丢的图白做一遍渲染。
    native: list[tuple[int, str, list[FigureRegion]]] = []
    #: 文字层不可信、但需要 OCR 才能替换的页。没有 OCR 能力时退回它们的文字层 ——
    #: 有缺陷的文字也好过什么都没有。
    untrusted: list[tuple[int, str]] = []

    for index, page in enumerate(pdf, start=1):
        classification = page_kind.classify_page(page)
        if classification.page_ocr:
            if not classification.text_layer_trusted:
                untrusted.append((index, sanitize_text(page.get_text("text")).strip()))
            # 整页扫描件上没有可独立提取的图（正文与插图是同一张位图），所以「配图」就是
            # 这一页本身。
            #
            # **优先直抽内嵌位图，而不是渲染页面。** 纯扫描页的内容就是那一张位图，渲染
            # 等于把它重新光栅化一遍：实测 190 ms/页，而直抽只要 24 ms/页（约 8 倍），
            # 且直抽用的是原生分辨率，不会把 96 dpi 的扫描插值放大成假的 150 dpi。
            # 页面里还有矢量内容时 `scan_page_image` 返回 None，退回渲染 —— 那一侧会漏内容。
            image = ocr.scan_page_image(page) or ocr.render_page(page)
            figure_format = image.extension.lstrip(".").lower() or "png"
            pending.append(
                (
                    index,
                    image,
                    [
                        ParsedFigure(
                            asset="",
                            page_number=index,
                            width=image.width,
                            height=image.height,
                            bbox=(page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1),
                            full_page=True,
                            payload=_figure_payload(image, figure_format),
                        )
                    ],
                )
            )
            continue
        text, regions = _page_text_and_regions(page, classification.signals.images)
        if text or regions:
            native.append((index, text, regions))

    recognized = _ocr_sections(pending)
    if untrusted:
        # OCR 没能覆盖这些页（不可用，或那一页没识别出文字）→ 用它们原本的文字层兜底，
        # 而不是让这些页在索引里消失。
        covered = {section.page_number for section in recognized}
        recognized.extend(
            ParsedSection(heading_path=[], markdown=text, page_number=page_number)
            for page_number, text in untrusted
            if page_number not in covered and text
        )
    # 识别结果必须并回来。这一行曾经在我重排时被误删，而症状是**整页扫描件与文字层不可信
    # 的页全部消失** —— 它们只走 pending 这条路，不进 native，所以没并回来就等于丢内容。
    sections.extend(recognized)
    # 先在整个文档范围内认出模板标记（项目符号、水印、角标），再从每一页里剔除。
    # 判据要跨文档统计才能成立：一颗项目符号单看某页只是一张小图，看整份文档才知道它每页
    # 都出现 —— 见 `figures.marker_digests` 的实测表。
    markers = marker_digests([region for _, _, regions in native for region in regions])
    for page_number, text, regions in native:
        kept = [region for region in regions if not is_marker(region, markers)]
        page = pdf[page_number - 1]
        figures = [
            rendered
            for rendered in (_render_figure(page, region) for region in kept)
            if rendered is not None
        ]
        ocr_text = _recognize_textless_figures(figures)
        # 图内文字层来自 `FigureRegion.text`（矢量图的轴名刻度）；替代文字与识别结果在
        # `ParsedFigure.caption` 上。两者都并进正文。
        labels = [
            region.text.strip() for region in kept if region.text.strip()
        ] + [figure.text for figure in figures]
        markdown = _merge_text(text, labels, ocr_text)
        if not markdown:
            continue
        sections.append(
            ParsedSection(
                heading_path=[], markdown=markdown, page_number=page_number, figures=figures
            )
        )
    sections.sort(key=lambda section: section.page_number or 0)
    return sections


def _merge_text(body: str, captions: list[str], ocr_texts: list[str]) -> str:
    """把图带来的文字并进正文，只并**新增**的部分。

    合并来源有三个，语义不同但目标一致 —— 让图里的字可被检索：

    * ``body``：页面文字层（可能为空，纯图页就是这种情况）。
    * ``captions``：图内文字层（矢量图的轴名、刻度、标注）与替代文字。
    * ``ocr_texts``：对「图里一个字都没有」的插图补做的识别结果。

    去重是必须的：图的文字常常就是正文的复制（图注、轴名来自同一段），整段并入会让同一
    内容被索引两遍，检索时返回看起来完全相同的引用。门槛与 PDF/Word/HTML 各通路一致。
    """
    parts = [body] if body else []
    seen = _tokens(body)
    for text in [*captions, *ocr_texts]:
        text = text.strip()
        if not text:
            continue
        tokens = _tokens(text)
        if tokens and len(tokens - seen) / len(tokens) < _FIGURE_TEXT_NOVELTY:
            continue
        seen |= tokens
        parts.append(text)
    return "\n\n".join(parts)


#: 图内文字与正文的重叠低于这个比例才并入。
#:
#: 一张图的文字层常常就是**从正文里复制过来的**（图注、轴名来自正文同一段）。精确比对
#: 抓不到这种重复：实测 05-Markov-Source p21 的图内文字与正文只差前两个字符，于是同一
#: 段内容被索引了两遍。按词集重叠率判断才拦得住。
_FIGURE_TEXT_NOVELTY = 0.4
_WORD = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]")


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD.finditer(text)}


def _page_text_and_regions(
    page: pymupdf.Page, images: Sequence[dict] = ()
) -> tuple[str, list[FigureRegion]]:
    """原生文字页的正文，以及**尚未渲染**的图形区域。

    这里刻意不渲染也不并图内文字：模板标记要按整份文档统计才能认出来，而渲染要花钱
    （扫描件上 190 ms/页）。调用方先过滤，再渲染和并入 —— 见 `_pdf_sections`。
    """
    body = sanitize_text(page.get_text("text")).strip()
    return body, detect_figures(page, page.number + 1, images=images)


def _figure_payload(image: ocr.RenderedImage, asset_format: str) -> bytes:
    """展示用的图片字节。

    **能原样存就原样存，不做转码。** 纯扫描页的内嵌位图本身就是压缩过的 JPEG
    （实测 216 KB/页），转成 PNG 要 72 ms/页、体积还膨胀到 437 KB —— 转码在这里是纯亏：
    既慢又大，还没有任何信息增益（JPEG 是有损的，转 PNG 不会把丢掉的信息找回来）。

    只有渲染出来的页面才需要编码，那本来就是 PNG。
    """
    if asset_format in {"png", "jpeg", "jpg"}:
        return image.pixels
    try:
        pixmap = pymupdf.Pixmap(image.pixels)
    except (RuntimeError, ValueError):
        return image.pixels
    return pixmap.tobytes("png")


def _recognize_textless_figures(figures: list[ParsedFigure]) -> list[str]:
    """给「图里一个字都没有」的插图补一次识别，并把结果并进它所属的 section。

    **补的是哪一类。** 原生文字页上的插图：页面文字层是真文字，所以整页不走识别；但**图里
    的字只存在于像素中** —— 一张光栅化的状态转移图、一张截图公式、一张示意图里的标注，
    在 ``page.get_text()`` 里一个字都没有。实测 6 份课件的 12 页抽样里这类图有 5 张，
    而且**全部**是面积比 ≥0.021、含位图的真内容（没有一张是装饰），所以不需要再设尺寸门槛。

    这正好覆盖用户最初提的场景：「论文中的图表、示意图」。

    整批一次调用：识别进程的启动开销按批摊，逐张调用在几十张图上就是几十秒。
    识别不可用时静默跳过 —— 那是补内容，不是文档成立的条件。
    """
    targets = [figure for figure in figures if figure.asset and figure.payload]
    if not targets:
        return []
    images: list[ocr.RenderedImage] = []
    usable: list[ParsedFigure] = []
    for figure in targets:
        try:
            pixmap = pymupdf.Pixmap(figure.payload)
        except (RuntimeError, ValueError):
            continue
        images.append(
            ocr.RenderedImage(
                pixels=figure.payload,
                width=float(pixmap.width),
                height=float(pixmap.height),
                page_rect=(0.0, 0.0, float(pixmap.width), float(pixmap.height)),
                page_number=figure.page_number or 0,
                extension="png",
            )
        )
        usable.append(figure)
    if not images:
        return []
    try:
        recognized = ocr.recognize(images)
    except ocr.OcrUnavailable:
        return []
    texts: list[str] = []
    for figure, page in zip(usable, recognized, strict=False):
        text = page.text().strip()
        # 识别结果进 `text`，**不进 `caption`**：`caption` 是"别人写的描述"（网页的
        # `alt`、Word 的替代文字），`text` 是"我们识别出来的"。两者语义不同，混用会让
        # 下游分不清一段文字是作者给的还是识别出的 —— 而且 HTML 那条通路写的是 `text`，
        # 不统一就会让读 `figure.text` 的代码在 PDF/Word 上永远拿到空串。
        figure.text = text
        if text:
            texts.append(text)
    return texts


def _render_figure(page: pymupdf.Page, figure: FigureRegion) -> ParsedFigure | None:
    """把一张图渲染成 PNG。

    渲染而不是抽取内嵌位图：图形常常是矢量画的（实测教材里 46% 的图是纯矢量，
    ``get_images()`` 一个位图都返回不了），抽位图会漏掉它们；而矢量图包着位图的情况也要
    一起框进来。渲染对两者一视同仁。

    渲染失败时返回 ``None`` 而不抛错：图看不到是遗憾，正文读不出来才是事故。
    """
    try:
        rendered = ocr.render_region(page, pymupdf.Rect(figure.bbox))
    except (RuntimeError, ValueError):
        return None
    digest = hashlib.sha256(rendered.pixels).hexdigest()
    return ParsedFigure(
        asset=f"{digest}.png",
        page_number=figure.page_number,
        caption=figure.text.strip(),
        width=int(rendered.width),
        height=int(rendered.height),
        bbox=figure.bbox,
        payload=rendered.pixels,
    )


def _ocr_sections(
    pending: list[tuple[int, ocr.RenderedImage, list[ParsedFigure]]],
) -> list[ParsedSection]:
    """把需要 OCR 的页整页识别成文本。

    OCR 不可用时**不抛错**：没有识别能力的机器仍然应该能导入文档，只是这些页的内容拿
    不到。这与 ``DocumentFormat.availability`` 是同一条约定 —— 能力缺失要降级，不要
    让整个导入失败。
    """
    if not pending:
        return []
    images = [image for _, image, _ in pending]
    try:
        recognized = ocr.recognize(images)
    except ocr.OcrUnavailable:
        return []
    except Exception:  # noqa: BLE001 - 识别后端异常不该让整篇文档导入失败
        return []

    sections: list[ParsedSection] = []
    for (page_number, image, figures), page in zip(pending, recognized, strict=False):
        lines = sorted(
            ocr.remap_to_page(page, image), key=lambda line: (line.center_y, line.center_x)
        )
        text = "\n".join(line.text for line in lines if line.text.strip()).strip()
        if not text:
            continue
        # 整页渲染图与页内插图走同一条落盘规则：文件名由像素内容决定。
        asset = hashlib.sha256(image.pixels).hexdigest()
        asset_format = image.extension.lstrip(".").lower() or "png"
        for figure in figures:
            figure.asset = f"{asset}.{asset_format}"
            # 整页扫描件这一页**就是**那张图，所以识别结果属于它。只把文字放进 markdown
            # 会让 `figure.text` 永远是空的 —— 而任何读 `figure.text` 的下游（去重、
            # 验证、未来的模型描述）都会在扫描书上拿到空串。
            figure.text = text
        sections.append(
            ParsedSection(
                heading_path=[],
                markdown=sanitize_text(text),
                page_number=page_number,
                figures=figures,
            )
        )
    return sections


def _from_markdown(document: DownloadedDocument, markdown: str, title: str) -> ParsedDocument:
    markdown = markdown.strip()
    return ParsedDocument(
        title=title or document.title,
        markdown=markdown,
        source_url=document.source_url,
        sections=sections_from_markdown(markdown),
    )


def _code_language(element: object) -> str | None:
    classes = getattr(element, "get", lambda _: [])("class") or []
    for class_name in classes:
        if class_name.startswith("language-"):
            return class_name.removeprefix("language-")
    return None


# -- payload recognition -----------------------------------------------------
#
# Each returns a message when the bytes are not that format, or None when they
# are. Returning a message rather than raising lets each caller wrap it in its
# own error code: the staging store reports SOURCE_UNSUPPORTED while the
# directory scanner reports BATCH_SOURCE_CHANGED for the same bad payload.


def _looks_like_pdf(raw: bytes) -> str | None:
    return None if raw.startswith(_PDF_MAGIC) else "PDF 文件签名无效"


def _looks_like_docx(raw: bytes) -> str | None:
    return None if raw.startswith(_ZIP_MAGIC) else "Word 文件签名无效"


def _decodes_as_utf8(raw: bytes) -> str | None:
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "Markdown 必须使用 UTF-8 编码"
    return None


def _decodes_as_utf8_strict(raw: bytes) -> str | None:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "文档必须使用 UTF-8 编码"
    return None


BUILTIN_FORMATS: tuple[DocumentFormat, ...] = (
    DocumentFormat(
        name="pdf",
        label="PDF",
        media_type=PDF_MEDIA_TYPE,
        extensions=(".pdf",),
        parse=parse_pdf,
        binary_payload=True,
        magic=_PDF_MAGIC,
        validate=_looks_like_pdf,
        # 唯一有原件阅读器的格式：pdfjs 直接按字节 Range 分页（见
        # api/documents.py 的 /raw 与 features/repositories/PdfOriginalView.tsx）。
        original_viewable=True,
    ),
    DocumentFormat(
        name="markdown",
        label="Markdown",
        media_type=MARKDOWN_MEDIA_TYPE,
        extensions=(".md", ".markdown"),
        parse=parse_markdown,
        content_types=("text/x-markdown", "text/plain"),
        validate=_decodes_as_utf8,
    ),
    DocumentFormat(
        name="docx",
        label="Word 文档",
        media_type=DOCX_MEDIA_TYPE,
        extensions=(".docx",),
        parse=parse_docx,
        binary_payload=True,
        magic=_ZIP_MAGIC,
        validate=_looks_like_docx,
    ),
    DocumentFormat(
        name="html",
        label="网页",
        media_type=HTML_MEDIA_TYPE,
        extensions=(".html", ".htm"),
        parse=parse_html,
        content_types=("application/xhtml+xml",),
        # Parsable, and allowed inside an imported directory, but a lone
        # `.html` is not offered as a document to pick.
        single_file=False,
        validate=_decodes_as_utf8_strict,
    ),
)


def builtin_registry() -> FormatRegistry:
    """A registry holding every format the application ships with.

    Built fresh per call: the application passes its own instance around so
    that plugin formats land in it, while standalone callers (a parser built in
    a test) get a registry that cannot have been mutated by anyone else.
    """
    registry = FormatRegistry()
    for format in BUILTIN_FORMATS:
        registry.register(format)
    return registry
