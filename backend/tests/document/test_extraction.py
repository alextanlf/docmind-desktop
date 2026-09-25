from __future__ import annotations

from bs4 import BeautifulSoup

from app.document.extraction import (
    extract_main_content,
    strip_document_noise,
    strip_hidden_content,
)
from app.document.parser import DocumentParser
from app.schemas.imports import DownloadedDocument

_ARTICLE_PAGE = """
<html><head><title>站点标题</title></head><body>
<nav class="site-nav"><a href="/a">首页</a><a href="/b">分类</a><a href="/c">关于</a></nav>
<div class="layout">
  <aside class="sidebar"><ul><li><a href="/1">侧栏推荐一</a></li><li><a href="/2">侧栏推荐二</a></li></ul></aside>
  <article class="post">
    <h1>真正的文章标题</h1>
    <p>{body}</p>
    <div class="share-tools"><a href="/share">分享到微博</a><a href="/share2">分享到微信</a></div>
  </article>
</div>
<footer class="site-footer"><p>版权所有</p></footer>
</body></html>
""".format(body="这是文章正文内容。" * 40)


def test_extract_main_content_prefers_article_over_page_chrome() -> None:
    soup = BeautifulSoup(_ARTICLE_PAGE, "html.parser")
    strip_hidden_content(soup)

    container = extract_main_content(soup)

    assert container is not None
    assert container.name == "article"
    strip_document_noise(container)
    text = container.get_text(" ", strip=True)
    assert "真正的文章标题" in text
    assert "这是文章正文内容" in text
    assert "侧栏推荐" not in text
    assert "分享到微博" not in text


def test_extract_main_content_falls_back_to_content_div() -> None:
    html = """
    <html><body>
      <div class="sidebar"><a href="/1">链接一</a><a href="/2">链接二</a><a href="/3">链接三</a></div>
      <div class="post-content"><h1>标题</h1><p>{body}</p></div>
    </body></html>
    """.format(body="正文段落。" * 60)
    soup = BeautifulSoup(html, "html.parser")
    strip_hidden_content(soup)

    container = extract_main_content(soup)

    assert container is not None
    assert container.get("class") == ["post-content"]
    assert "正文段落" in container.get_text(" ", strip=True)


def test_extract_main_content_returns_none_for_short_pages() -> None:
    soup = BeautifulSoup("<html><body><h1>短页面</h1><p>只有一点内容</p></body></html>", "html.parser")
    strip_hidden_content(soup)

    assert extract_main_content(soup) is None


def test_extract_main_content_rejects_tiny_fragments_of_long_pages() -> None:
    html = """
    <html><body>
      <div class="comments"><p>{comment}</p></div>
      {body}
    </body></html>
    """.format(comment="评论内容。" * 60, body="<p>正文段落。</p>" * 600)
    soup = BeautifulSoup(html, "html.parser")
    strip_hidden_content(soup)

    assert extract_main_content(soup) is None


def test_html_parser_keeps_article_and_drops_surrounding_noise() -> None:
    document = DownloadedDocument(
        title="article.html",
        source_url="https://docs.test/article",
        media_type="text/html",
        raw_bytes=_ARTICLE_PAGE.encode(),
    )

    parsed = DocumentParser().parse(document)

    assert "真正的文章标题" in parsed.markdown
    assert "这是文章正文内容" in parsed.markdown
    assert "侧栏推荐" not in parsed.markdown
    assert "分享到微博" not in parsed.markdown
    assert "版权所有" not in parsed.markdown
