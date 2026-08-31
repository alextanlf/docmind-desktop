from __future__ import annotations

import re

import fitz
from bs4 import BeautifulSoup
from markdownify import markdownify

from app.api.errors import DomainError
from app.schemas.imports import DownloadedDocument, ParsedDocument, ParsedSection

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class DocumentParser:
    def parse(self, document: DownloadedDocument) -> ParsedDocument:
        if document.media_type == "application/pdf":
            return self._parse_pdf(document)
        if document.media_type == "text/markdown":
            try:
                markdown = document.raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise DomainError("SOURCE_UNSUPPORTED", "Markdown 必须使用 UTF-8 编码", 400, False) from None
            return self._from_markdown(document, markdown, document.title)
        if document.media_type == "text/html":
            return self._parse_html(document)
        raise DomainError("SOURCE_UNSUPPORTED", "不支持的文档类型", 400, False)

    def _parse_html(self, document: DownloadedDocument) -> ParsedDocument:
        soup = BeautifulSoup(document.raw_bytes, "html.parser")
        for node in soup.select("script, style, nav, footer, aside, template, [hidden], [aria-hidden='true']"):
            node.decompose()
        for node in soup.select("[style]"):
            if "display:none" in node.get("style", "").replace(" ", "").lower():
                node.decompose()
        for node in soup.find_all():
            if not node.get_text(" ", strip=True) and not node.find(["img", "br", "hr"]):
                node.decompose()
        title_node = soup.find("h1") or soup.title
        title = title_node.get_text(" ", strip=True) if title_node else document.title
        if soup.head:
            soup.head.decompose()
        markdown = markdownify(str(soup), heading_style="ATX", code_language_callback=self._code_language).strip()
        languages = [self._code_language(code) for code in soup.select("pre > code")]
        language_index = 0

        def add_code_language(match: re.Match[str]) -> str:
            nonlocal language_index
            language = languages[language_index] if language_index < len(languages) else None
            language_index += 1
            return f"```{language or ''}{match.group(1)}```"

        markdown = re.sub(r"```(\n.*?\n)```", add_code_language, markdown, flags=re.DOTALL)
        return self._from_markdown(document, markdown, title or document.title)

    @staticmethod
    def _code_language(element: object) -> str | None:
        classes = getattr(element, "get", lambda _: [])("class") or []
        for class_name in classes:
            if class_name.startswith("language-"):
                return class_name.removeprefix("language-")
        return None

    def _parse_pdf(self, document: DownloadedDocument) -> ParsedDocument:
        if not document.raw_bytes.startswith(b"%PDF-"):
            raise DomainError("SOURCE_UNSUPPORTED", "PDF 文件签名无效", 400, False)
        try:
            pdf = fitz.open(stream=document.raw_bytes, filetype="pdf")
        except (RuntimeError, ValueError):
            raise DomainError("SOURCE_UNSUPPORTED", "PDF 文件无效", 400, False) from None
        try:
            sections = [
                ParsedSection(heading_path=[], markdown=text, page_number=index)
                for index, page in enumerate(pdf, start=1)
                if (text := page.get_text("text").strip())
            ]
        finally:
            pdf.close()
        markdown = "\n\n".join(section.markdown for section in sections)
        return ParsedDocument(title=document.title, markdown=markdown, source_url=document.source_url, sections=sections)

    def _from_markdown(self, document: DownloadedDocument, markdown: str, title: str) -> ParsedDocument:
        markdown = markdown.strip()
        sections = self._sections_from_markdown(markdown)
        return ParsedDocument(title=title, markdown=markdown, source_url=document.source_url, sections=sections)

    @staticmethod
    def _sections_from_markdown(markdown: str) -> list[ParsedSection]:
        sections: list[ParsedSection] = []
        path: list[str] = []
        current: list[str] = []
        current_path: list[str] = []
        in_fence = False
        for line in markdown.splitlines():
            if line.startswith(("```", "~~~")):
                in_fence = not in_fence
            heading = None if in_fence else _HEADING.match(line)
            if heading:
                if current and "\n".join(current).strip():
                    sections.append(ParsedSection(heading_path=current_path, markdown="\n".join(current).strip()))
                level = len(heading.group(1))
                text = heading.group(2).strip().rstrip("#").strip()
                path = path[: level - 1] + [text]
                current_path = list(path)
                current = [line]
            else:
                current.append(line)
        if current and "\n".join(current).strip():
            sections.append(ParsedSection(heading_path=current_path, markdown="\n".join(current).strip()))
        return sections
