"""用**真实网页**验证 HTML 图片通路：抽取 → 下载 → 识别 → 并入正文。

**为什么需要它。** HTML 那条通路此前只在合成的 mock transport 上端到端跑过 —— 页面是自己
拼的、图片是内存里造的。真实网页有好几件合成样本里没有的事：

* 图片**几乎全在另一个主机**（实测维基百科 26 个 `<img>` 里，内容图全部在
  `thumb.wikimedia.org`）。所以"只抓同主机"不是"覆盖更全"的取舍，而是**能不能用**的取舍。
* `srcset` 与协议相对 URL（`//host/path`）混用。
* 装饰图靠路径与声明尺寸过滤，而真实页面里装饰图与内容图的比例与合成样本不同。
* 识别真实位图（矢量图导出的 PNG 缩略图、截图）与识别合成渐变图完全是两回事。

所以这支脚本把页面**真实地**走一遍：真实 HTML、真实图片字节、真实识别。它需要联网。

用法::

    uv run python scripts/verify_html_figures_live.py --url https://en.wikipedia.org/wiki/Operating_system
    uv run python scripts/verify_html_figures_live.py --page /path/to/saved.html --url <原始URL>

``--page`` 用于离线复现：把一个之前存下来的页面喂进来，地址仍按 ``--url`` 解析（相对
地址要靠它）。此时图片仍会从网络下载。
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.document.builtin_formats import parse_html
from app.document.html_figures import fetch_pending
from app.document.safe_http import SafeHttpClient
from app.schemas.imports import DownloadedDocument

#: 承载页面本身的主机名。真实主机名不能指到本机（SSRF 防护会拦），所以页面用这个名字
#: 由 mock transport 提供，而图片仍走真实网络 —— 见下面 `_PageTransport` 的说明。
PAGE_HOST = "en.wikipedia.org"

#: 抓页面与抓图片用同一套请求头。维基媒体对没有合规 User-Agent 的请求返回 403，
#: 分两套头会让图片全部失败。
USER_AGENT = {"user-agent": "DocMind/2 (https://example.invalid/docmind; contact@example.invalid)"}


class _PageTransport(httpx.AsyncBaseTransport):
    """只接管承载页面的那个主机名，其余请求走真实网络（经代理，如果配了）。

    这样做的原因：`SafeHttpClient` 会拒绝解析到本机/内网的地址，而把真实页面挂在
    `127.0.0.1` 上就会被它正确地拦下来。所以页面用真实主机名 + mock 字节提供，
    **图片则走真实 HTTP** —— 要验证的是图片那条路，页面字节从哪来不影响它。
    """

    def __init__(self, body: bytes, media_type: str, proxy: str | None) -> None:
        self.body = body
        self.media_type = media_type
        self.real = httpx.AsyncHTTPTransport(proxy=_proxy(proxy))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == PAGE_HOST:
            return httpx.Response(
                200, headers={"content-type": self.media_type}, content=self.body
            )
        return await self.real.handle_async_request(request)

    async def aclose(self) -> None:
        await self.real.aclose()


class _PublicResolver:
    """把一切解析成全局地址。

    只为绕过"本机地址必须被拒"这一条 —— 那一条本身是对的（它是 SSRF 防护），这里要做的是
    图片下载，而图片地址本来就都在公网 CDN 上。
    """

    async def resolve(self, host: str):
        return [ipaddress.ip_address("93.184.216.34")]


def _proxy(value: str | None) -> httpx.Proxy | None:
    """把代理字符串转成 httpx 的 Proxy 对象。

    `httpx.AsyncHTTPTransport(proxy=...)` 只接受 `Proxy`，传字符串会被当成别的东西直接
    忽略 —— 症状是所有请求都失败、错误还是"抓取失败"，看不出是代理没生效。
    """
    return httpx.Proxy(value) if value else None


def _load(args: argparse.Namespace) -> tuple[bytes, str, str]:
    """返回 (页面字节, 媒体类型, 页面地址)。"""
    if args.page is not None:
        raw = args.page.read_bytes()
        media_type = "text/html"
    else:
        with httpx.Client(proxy=args.proxy, follow_redirects=True, timeout=30.0) as client:
            response = client.get(args.url, headers=USER_AGENT)
            response.raise_for_status()
            raw = response.content
            media_type = response.headers.get("content-type", "text/html").split(";")[0]
        # 重定向之后真实地址会变，相对地址要按最终地址解析。
        args.url = str(response.url)
    return raw, media_type, args.url


async def run(args: argparse.Namespace) -> int:
    raw, media_type, url = _load(args)
    print(f"页面 {url}\n  {len(raw)} 字节  {media_type}")

    document = DownloadedDocument(
        title="live page", source_url=url, media_type=media_type, raw_bytes=raw
    )
    parsed = parse_html(document)
    figures = [figure for section in parsed.sections for figure in section.figures]
    print(f"\n解析：{len(parsed.sections)} 节、{len(parsed.markdown)} 字符、待下载图 {len(figures)} 张")
    for figure in figures[:12]:
        print(f"   {urlsplit(figure.source_url).hostname:28} {figure.source_url.rsplit('/', 1)[-1][:44]}")

    client = SafeHttpClient(
        resolver=_PublicResolver(), transport=_PageTransport(raw, media_type, args.proxy)
    )
    fetched = await fetch_pending(parsed, client=client, headers=USER_AGENT)
    figures = [figure for section in parsed.sections for figure in section.figures]

    print(f"\n下载并识别：{fetched} 张成功")
    recognized = 0
    for figure in figures:
        # 识别结果在 `text` 上；`caption` 是页面自己给的替代文字（`alt`），可能为空也
        # 可能写着别的东西。两者语义不同，不能混着看。
        text = figure.text.strip()
        if not figure.asset:
            print(f"   下载失败：{figure.failure or '原因未知'}")
            continue
        if text:
            recognized += 1
        first = text.replace("\n", " / ")[:76]
        print(f"   {len(figure.payload):>7} 字节  {figure.asset[:12]}…  文字 {len(text):>4} 字符  {first}")

    # 图内文字有没有进正文 —— 那才是"能被搜到"的前提。
    merged = sum(1 for figure in figures if figure.text.strip() and figure.text.strip() in parsed.markdown)
    print(
        f"\n汇总：{fetched} 张下载成功、{recognized} 张识别出文字、"
        f"{merged} 张的文字并入了正文"
    )
    print(f"正文 {len(parsed.markdown)} 字符（含图内文字）")

    if args.json:
        import json

        args.json.write_text(
            json.dumps(
                {
                    "url": url,
                    "sections": len(parsed.sections),
                    "markdown_chars": len(parsed.markdown),
                    "pending": len(figures),
                    "fetched": fetched,
                    "recognized": recognized,
                    "merged": merged,
                    "figures": [
                        {
                            "url": figure.source_url,
                            "host": urlsplit(figure.source_url).hostname,
                            "asset": figure.asset,
                            "bytes": len(figure.payload),
                            "text": figure.text,
                            "alt": figure.caption,
                            "failure": figure.failure,
                        }
                        for figure in figures
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="页面地址（相对图片按它解析）")
    parser.add_argument("--page", type=Path, default=None, help="离线页面文件（配合 --url）")
    parser.add_argument("--proxy", default=None, help="HTTP 代理，例如 http://127.0.0.1:7890")
    parser.add_argument("--json", type=Path, default=None)
    arguments = parser.parse_args()
    return asyncio.run(run(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
