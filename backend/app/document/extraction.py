from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

_STRIP_TAGS = (
    "script",
    "style",
    "nav",
    "footer",
    "aside",
    "form",
    "iframe",
    "noscript",
    "template",
    "svg",
    "canvas",
    "button",
)
_STRIP_ROLES = frozenset(
    {
        "navigation",
        "banner",
        "complementary",
        "contentinfo",
        "search",
        "menu",
        "menubar",
        "dialog",
        "alert",
    }
)
_NOISE_HINT = re.compile(
    r"(?:^|[-_ ])(?:nav(?:igation)?|menu|sidebar|footer|comment|comments|share|social"
    r"|related|recommend|breadcrumb|pagination|toolbar|advert|ads?|promo|cookie|subscribe"
    r"|newsletter|login|signup|banner)(?:$|[-_ ])",
    re.IGNORECASE,
)
_MAIN_HINT = re.compile(
    r"article|content|post|entry|main|story|markdown|rich_?text", re.IGNORECASE
)
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)(?:display:none|visibility:hidden)(?:!important)?(?:;|$)"
)
#: 即使没有文本也要保留的标签。它们本身承载内容（图片、换行、分隔线）。
_KEEP_TAGS = frozenset({"img", "br", "hr", "source", "video", "audio", "iframe", "svg", "canvas"})
_MIN_TEXT = 200
_DESCEND_RATIO = 0.8
_BODY_RATIO = 0.15


def strip_hidden_content(soup: BeautifulSoup) -> None:
    """Remove script/navigation/hidden/empty nodes before extraction (existing behaviour)."""
    for node in soup.select(
        "script, style, nav, footer, aside, template, [hidden], [aria-hidden='true']"
    ):
        node.decompose()
    for node in soup.select("[style]"):
        attributes = getattr(node, "attrs", None)
        if not isinstance(attributes, dict):
            continue
        style = re.sub(r"\s+", "", (attributes.get("style") or "").lower())
        if _HIDDEN_STYLE.search(style):
            node.decompose()
    for node in soup.find_all():
        # `node.find(...)` 查的是**子孙**，不含 node 自己。只写 `not node.find([...])`
        # 会把 `<img>` 自身删掉 —— 它没有文本、也没有 img 子孙，于是被当空节点。实测这
        # 让网页里的**每一张图**都在解析阶段消失（`markdownify` 自然也就输出不了它们），
        # 整条图片通路静默失效。
        if node.get_text(" ", strip=True):
            continue
        # 自身是保留标签（`<img>`、`<br>`），或含有保留标签的子孙（`<picture>` 包着
        # `<source>`），都要留下。
        if node.name in _KEEP_TAGS or node.find(list(_KEEP_TAGS)):
            continue
        node.decompose()


def extract_main_content(soup: BeautifulSoup) -> Tag | None:
    """Return the most likely article container, or None to keep the whole page."""
    candidates: list[tuple[float, int, Tag]] = []
    for node in soup.find_all(["article", "main", "div", "section", "td"]):
        if not isinstance(node, Tag):
            continue
        length = len(node.get_text(" ", strip=True))
        if length < _MIN_TEXT:
            continue
        candidates.append((_score(node, length), length, node))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    node = _descend(candidates[0][2])
    body = soup.body or soup
    body_length = len(body.get_text(" ", strip=True))
    node_length = len(node.get_text(" ", strip=True))
    if body_length and node_length < max(_MIN_TEXT, body_length * _BODY_RATIO):
        return None
    return node if node is not soup else None


def strip_document_noise(node: Tag) -> None:
    """Drop navigation-ish descendants inside the chosen article container."""
    for child in list(node.find_all(True)):
        if _is_noise(child):
            child.decompose()


def _score(node: Tag, length: int) -> float:
    link_text = sum(len(link.get_text(" ", strip=True)) for link in node.find_all("a"))
    link_density = min(link_text / max(length, 1), 1.0)
    score = length * (1.0 - link_density)
    hint = _hint(node)
    if _MAIN_HINT.search(hint):
        score *= 1.4
    if node.name in {"article", "main"}:
        score *= 1.5
    if _NOISE_HINT.search(hint):
        score *= 0.25
    return score


def _descend(node: Tag) -> Tag:
    current = node
    while True:
        target = len(current.get_text(" ", strip=True))
        best: Tag | None = None
        best_length = 0
        for child in current.find_all(["article", "main", "div", "section"], recursive=False):
            child_length = len(child.get_text(" ", strip=True))
            if child_length < max(_MIN_TEXT, target * _DESCEND_RATIO) or child_length <= best_length:
                continue
            best, best_length = child, child_length
        if best is None:
            return current
        current = best


def _is_noise(tag: Tag) -> bool:
    if getattr(tag, "attrs", None) is None:
        return True
    if tag.name in _STRIP_TAGS:
        return True
    if str(tag.get("role") or "").lower() in _STRIP_ROLES:
        return True
    return bool(_NOISE_HINT.search(_hint(tag)))


def _hint(node: Tag) -> str:
    classes = node.get("class") or []
    if not isinstance(classes, list):
        classes = [str(classes)]
    return " ".join([str(node.get("id") or ""), *classes]).strip()
