"""网页里的图片：抽出引用、随后下载并识别。

**为什么单独一层。** PDF 与 Word 的图就在文档字节里，解析时随手就能拿到。网页不同：
``<img src>`` 是一个 URL，字节在别人的服务器上 —— 抽取是同步的、下载是异步的，两件事
必须分开，否则解析器要变成异步的，牵动整条导入链路。

所以沿用 ``page_ocr`` 那条既有模式：解析时把引用收集成"待补全"的插图，导入服务在
解析之后补上字节与文字（见 ``app/imports/service.py``）。

**安全与隐私是这个模块的主要约束，不是附带的。** 抓取网页里的图意味着应用会主动访问
第三方主机：

* **SSRF 必须挡。** ``<img src="http://127.0.0.1:18900/api/...">`` 会让应用去请求自己的
  后端，而 ``SourceValidator`` 只拦字面 IP、不拦「解析到私网的域名」（DNS rebinding）。
  所以字节层走 :class:`app.document.safe_http.SafeHttpClient`，它在连接前解析并拒绝
  任何非全局地址。
* **图片主机能看到用户的网络地址。** 这是产品层面的取舍，已按此修订 README 的隐私说明：
  导入具体网页时会从该页面引用的图片主机下载图片。
* **不跟随重定向。** 重定向可以把请求引到另一台主机上，而"允许哪些主机"的判断是在跳转
  **前**做的。图片是装饰性资源，拿不到就算了。
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import pymupdf
from bs4 import BeautifulSoup, Tag

from app.api.errors import DomainError
from app.document import ocr
from app.document.safe_http import SafeHttpClient
from app.schemas.imports import ParsedDocument, ParsedFigure

#: 单张图的大小上限。
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: 一个页面最多下载多少张图。防止一个塞满图的页面把导入拖成几分钟。
MAX_IMAGES_PER_PAGE = 40
#: 一个页面的图片总字节上限。
MAX_TOTAL_IMAGE_BYTES = 24 * 1024 * 1024
#: 下载图片的超时（秒）。
IMAGE_TIMEOUT_SECONDS = 15.0
#: 小于这个字节数的图不当内容：图标、间隔条、装饰线都在这个量级以下。
MIN_IMAGE_BYTES = 4 * 1024
#: 页面上显式声明宽度小于这个像素数的图不当内容。
#:
#: 这是从**标记**上就能判断的那一半：`<img width="16">` 是图标，不必下载就知道。它与
#: `MIN_IMAGE_BYTES` 互补 —— 后者要下载之后才量得到，而下载是有代价的（网络 + 对第三方
#: 主机的访问）。先按便宜的信息挡掉一批。
#:
#: 100 这个门槛取的是"小于它基本不可能是内容图"：正文插图在页面里通常按几百像素渲染，
#: 而图标类（logo、社交按钮、项目符号）几乎都在 100 以下。只用 `width` 属性，不用
#: `srcset` 的尺寸描述符 —— 那个是给不同屏幕密度用的，与显示尺寸无关。
MIN_DECLARED_WIDTH = 100

#: 只下这些类型。SVG 是矢量，识别引擎读不了；`data:` 内联图不走网络，单独处理。
_RASTER_MEDIA_PREFIX = "image/"
_SUPPORTED_MEDIA = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif", "image/bmp", "image/tiff")

#: `srcset` 里的候选：`url 1.5x` 或 `url 800w`，按逗号分隔。
_SRCSET_SPLIT = re.compile(r"\s*,\s*")
_SRCSET_PART = re.compile(r"^(?P<url>\S+)(?:\s+(?P<descriptor>\d+(?:\.\d+)?[wx]))?$")


@dataclass
class HtmlImageRef:
    """网页里引用的图片，以及把它补全所需的信息。

    ``payload`` 与 ``text`` 由导入服务随后填上：解析阶段不做网络访问。
    """

    #: 解析后的绝对 URL，作为去重键。
    url: str
    alt: str = ""
    #: 显式声明的显示宽度（像素）。用来过滤小的装饰图；`srcset` 的尺寸描述符不算。
    width: int = 0
    text: str = ""
    #: 抓取失败的原因，用于诊断（不阻断导入）。
    failure: str = ""
    #: 同一张图在一个页面里出现多次时的引用计数。
    references: int = 1


def _best_srcset_candidate(srcset: str) -> str:
    """从 `srcset` 里挑分辨率最高的那个 URL。

    挑最大的而不是第一个：`srcset` 的候选按尺寸排列，第一个常常是给窄屏用的缩略图，
    里面的文字小到识别不出来。
    """
    best_url, best_weight = "", -1.0
    for part in _SRCSET_SPLIT.split(srcset.strip()):
        match = _SRCSET_PART.match(part.strip())
        if not match:
            continue
        descriptor = match.group("descriptor") or ""
        if descriptor.endswith("w"):
            weight = float(descriptor[:-1])
        elif descriptor.endswith("x"):
            weight = float(descriptor[:-1]) * 1000.0
        else:
            weight = 0.0
        if weight > best_weight:
            best_url, best_weight = match.group("url"), weight
    return best_url


def _candidate_url(image: Tag) -> str:
    """一张 `<img>` 的候选地址。优先 `srcset` 里最大的，其次常见懒加载属性。"""
    srcset = image.get("srcset") or image.get("data-srcset") or ""
    if isinstance(srcset, str) and srcset.strip():
        candidate = _best_srcset_candidate(srcset)
        if candidate:
            return candidate
    # 懒加载属性排在 `src` 前面：开懒加载的页面里 `src` 常常是 1×1 占位图，真地址在
    # `data-src` 这类属性上。`src` 放在最后是没有懒加载属性时的兜底。
    for attribute in ("data-src", "data-original", "data-lazy-src", "src"):
        value = image.get(attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _declared_width(image: Tag) -> int:
    for attribute in ("width", "data-width"):
        value = image.get(attribute)
        if isinstance(value, str) and value.strip().rstrip("px").isdigit():
            return int(value.strip().rstrip("px"))
        if isinstance(value, int):
            return value
    return 0


def extract_image_refs(soup: BeautifulSoup, base_url: str) -> list[HtmlImageRef]:
    """列出页面引用的图片，按 URL 去重。

    ``<img>`` 与 ``<source>`（`<picture>` 里）都算。带 ``aria-hidden`` 的、以及 ``alt``
    为空的 logo 类图仍然会被收集 —— 是否值得留下交给后面的尺寸与内容判断，不在抽取阶段
    就替用户决定。
    """
    refs: dict[str, HtmlImageRef] = {}
    for image in soup.find_all(["img", "source"]):
        if not isinstance(image, Tag):
            continue
        candidate = _candidate_url(image)
        if not candidate:
            continue
        stripped = candidate.strip()
        if stripped.startswith("data:"):
            # 内联图不走网络，单独一条通路；这里先跳过，避免把 base64 当 URL。
            continue
        try:
            absolute = urljoin(base_url, stripped)
        except ValueError:
            continue
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        existing = refs.get(absolute)
        if existing is not None:
            existing.references += 1
            continue
        alt = image.get("alt")
        refs[absolute] = HtmlImageRef(
            url=absolute,
            alt=alt.strip() if isinstance(alt, str) else "",
            width=_declared_width(image),
        )
    return list(refs.values())


def is_fetchable(url: str) -> tuple[bool, str]:
    """这张图值不值得下载。返回 ``(是否下载, 原因)``。

    挡掉的都是明确无意义或明确有风险的：

    * **SVG**：矢量格式，识别引擎读不了；网页里绝大多数 SVG 是 logo 与图标。
    * **显式声明得很小**（宽 < 100px）：图标、间隔条。
    * **明显是装饰的路径**：`/static/images/icons/`、`sprite`、`logo` 这类。
    """
    parts = urlsplit(url)
    path = parts.path.lower()
    if path.endswith((".svg", ".svgz")):
        return False, "矢量图（SVG）读不了"
    # 字面私网地址在这里就挡掉。真正的 SSRF 防线是 `SafeHttpClient._check_host`
    # （它连解析到私网的**域名**都拦），但那是要发起请求时才生效的一层；一个明显指向
    # 本机的 URL 没有理由走到那里。这是纵深防御，不是替代品。
    host = parts.hostname or ""
    if host and not _literal_host_is_public(host):
        return False, "地址指向本机或内网"
    if any(hint in path for hint in ("/icon", "sprite", "logo", "/static/", "favicon", "avatar", "badge")):
        return False, "看起来是装饰图"
    return True, ""


def is_too_small_declared(width: int) -> bool:
    """页面上声明了宽度、且小到不可能是内容图。"""
    return 0 < width < MIN_DECLARED_WIDTH


#: 明确指向本机、且解析起来很便宜的主机名。
_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})


def _literal_host_is_public(host: str) -> bool:
    """主机名是否可能是本机/内网。

    字面 IP 直接判断；`localhost` 与 `.localhost` 是保留给本机的名字，也一并挡掉。
    其余域名返回 True —— 它们要解析之后才知道，那是 `SafeHttpClient._check_host` 的职责。
    """
    lowered = host.lower().rstrip(".")
    if lowered in _LOCAL_HOSTS or lowered.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return True
    return address.is_global


def _looks_like_image(payload: bytes) -> bool:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if payload.startswith(b"\xff\xd8\xff"):
        return True
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return True
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return True
    if payload.startswith(b"BM"):
        return True
    return payload[:4] in {b"II*\x00", b"MM\x00*"}


def image_media_type(payload: bytes) -> str | None:
    """按内容判断类型。不信任服务器给的 `Content-Type`。"""
    if not _looks_like_image(payload):
        return None
    if payload.startswith(b"\x89PNG"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if payload.startswith(b"RIFF"):
        return "image/webp"
    if payload.startswith(b"BM"):
        return "image/bmp"
    return "image/tiff"


def _to_png(payload: bytes) -> bytes | None:
    """规范成 PNG。识别不了返回 None。

    网页图片的容器格式很杂（PNG/JPEG/WebP/GIF/BMP/TIFF），统一转 PNG 之后落盘、展示与
    命名都只有一种情况要处理 —— 不像 PDF 那条通路，那里的内嵌 JPEG 原样存更省（体积小
    一半、还省掉转码），因为它本来就是扫描件。
    """
    try:
        pixmap = pymupdf.Pixmap(payload)
    except (RuntimeError, ValueError):
        return None
    return pixmap.tobytes("png")


async def fetch_pending(
    parsed: ParsedDocument,
    *,
    client: SafeHttpClient,
    timeout: float = IMAGE_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
) -> int:
    """下载并识别网页里那些还没拿到字节的图，返回成功补全的张数。

    **走 `SafeHttpClient` 而不是直接 httpx。** 网页里可以写
    ``<img src="http://127.0.0.1:18900/api/...">``，而
    :class:`app.document.sources.SourceValidator` 只拦字面 IP、**不拦解析到私网的域名**
    （DNS rebinding）。`SafeHttpClient._check_host` 在连接前解析并拒绝任何非全局地址，
    这才是图片抓取需要的那道门。

    **不跟随重定向。** 「允许哪些主机」的判断是在跳转前做的；跟随重定向等于把这个判断
    交给对方服务器。图片是装饰性资源，拿不到就算了。

    单张失败只记原因、继续下一张：一个页面上坏掉几张图不该让整篇文档导入失败。

    ``headers`` 要带上抓页面时用的那套。**这不是可选的礼貌问题**：实测维基百科的图片
    CDN 对没有合规 User-Agent 的请求直接返回 403，于是整页的图全部下载失败 —— 而失败
    原因只会显示成"抓取失败"，看不出是 UA 的问题。抓页面用的头，抓它的图也要用。
    """
    pending = [
        figure
        for section in parsed.sections
        for figure in section.figures
        if figure.source_url and not figure.payload
    ]
    if not pending:
        return 0
    budget = MAX_TOTAL_IMAGE_BYTES
    rendered: list[tuple[ParsedFigure, ocr.RenderedImage]] = []
    for figure in pending[:MAX_IMAGES_PER_PAGE]:
        try:
            async with asyncio.timeout(timeout):
                snapshot = await client.get(
                    figure.source_url, max_bytes=MAX_IMAGE_BYTES, headers=headers
                )
        except (TimeoutError, DomainError) as error:
            figure.failure = getattr(error, "code", "") or "抓取失败"
            continue
        payload = snapshot.body
        if len(payload) < MIN_IMAGE_BYTES:
            figure.failure = "图片过小"
            continue
        if len(payload) > budget:
            figure.failure = "超过本页图片总大小上限"
            continue
        if image_media_type(payload) is None:
            figure.failure = "不是可识别的图片格式"
            continue
        budget -= len(payload)
        normalized = _to_png(payload)
        if normalized is None:
            figure.failure = "图片无法解码"
            continue
        figure.payload = normalized
        figure.asset = f"{hashlib.sha256(figure.payload).hexdigest()}.png"
        try:
            pixmap = pymupdf.Pixmap(figure.payload)
        except (RuntimeError, ValueError):
            continue
        rendered.append(
            (
                figure,
                ocr.RenderedImage(
                    pixels=figure.payload,
                    width=float(pixmap.width),
                    height=float(pixmap.height),
                    page_rect=(0.0, 0.0, float(pixmap.width), float(pixmap.height)),
                    page_number=0,
                ),
            )
        )

    if rendered:
        try:
            recognized = ocr.recognize([image for _, image in rendered])
        except ocr.OcrUnavailable:
            # 没有识别能力的机器照样要能导入网页 —— 图内文字拿不到而已。
            recognized = []
        for (figure, _), page in zip(rendered, recognized, strict=False):
            figure.text = page.text().strip()

    # 正文并入：只并**新增**的，理由与 PDF/Word 两条通路相同 —— 图的文字常常就是正文的
    # 复制，整段并入会让同一内容被索引两遍。
    # **顺序不能反：先归属，再并入。**
    #
    # 抽取阶段把所有图都挂在第一节（那时还没有文字可以判断归属）。如果先并入，`_merge_text`
    # 会把**每一个**图的文字并进**每一节** —— 实测一个维基百科页面因此让同一段 MS-DOS
    # 截图文字出现在 9 个不同的节里，每节一份，正文从 120017 字符虚涨到 159003。
    # 那不只是数字难看：检索会在 9 个位置返回同一段内容。
    #
    # 归属之后再并入，每张图的文字只进它真正属于的那一节。
    _reassign_by_text(parsed)
    _merge_text_into_sections(parsed, [figure for figure, _ in rendered])
    return len(rendered)


def _merge_text_into_sections(parsed: ParsedDocument, figures: list[ParsedFigure]) -> None:
    """把每张图的文字并进它**所属的那一节**，只并新增的部分。

    **必须按"这一节自己的图"来并，不能拿文档级的图列表去遍历每一节。** 后者是
    「所有节 × 所有图」的笛卡尔积：实测一个维基百科页面因此让同一段 MS-DOS 截图文字出现
    在 9 个不同的节里，正文从 120017 字符虚涨到 159003。后果不只是数字难看 —— 检索会在
    9 个位置返回同一段内容。

    调用方仍须先做归属（`_reassign_by_text`）：抽取阶段所有图都挂在第一节，那时按节取图
    等于全给第一节。
    """
    document_additions: list[str] = []
    for section in parsed.sections:
        seen = _tokens(section.markdown)
        additions: list[str] = []
        # 只处理**挂在这一节上**的图。文档级的 `figures` 只用来判断"哪些图已经被处理过"，
        # 避免同一张图的文字被两处认领。
        for figure in [item for item in figures if item in section.figures]:
            if not figure.text:
                continue
            tokens = _tokens(figure.text)
            if tokens and len(tokens - seen) / len(tokens) < _FIGURE_TEXT_NOVELTY:
                continue
            seen |= tokens
            additions.append(figure.text)
        if additions:
            section.markdown = "\n\n".join([section.markdown, *additions]).strip()
            document_additions.extend(additions)
    if document_additions:
        parsed.markdown = "\n\n".join([parsed.markdown, *document_additions]).strip()


def _reassign_by_text(parsed: ParsedDocument) -> None:
    """按图内文字与各节的重合度重新分配插图。

    抽取阶段把所有图都挂在第一节（那时还没有文字）；识别之后就按内容归属，让图出现在
    介绍它的那一节旁边。与 Word 那条通路同一套判据。
    """
    if len(parsed.sections) < 2:
        return
    figures = [figure for section in parsed.sections for figure in section.figures]
    if not figures:
        return
    for section in parsed.sections:
        section.figures = []
    section_tokens = [_tokens(section.markdown) for section in parsed.sections]
    for figure in figures:
        figure_tokens = _tokens(figure.text) | _tokens(figure.caption)
        best_index, best_overlap = 0, 0.0
        if figure_tokens:
            for index, tokens in enumerate(section_tokens):
                if not tokens:
                    continue
                overlap = len(figure_tokens & tokens) / len(figure_tokens)
                if overlap > best_overlap:
                    best_index, best_overlap = index, overlap
        parsed.sections[best_index].figures.append(figure)


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD.finditer(text)}


_WORD = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]")
#: 与 PDF/Word 通路同一门槛：图的文字与正文重叠超过这个比例就不并入。
_FIGURE_TEXT_NOVELTY = 0.4
