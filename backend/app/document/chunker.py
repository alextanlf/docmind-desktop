from __future__ import annotations

import re

from app.schemas.imports import DocumentChunkDraft, ParsedDocument, ParsedSection


class ApproxTokenCounter:
    def count(self, text: str) -> int:
        cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        non_cjk_words = len(re.findall(r"[A-Za-z0-9_]+|[^\s\w]", text))
        return cjk + non_cjk_words


# 概览块在 section_path 上的固定标记。检索命中后前端与 prompt 都能据此识别它是「文档级概览」
# 而不是正文片段，从而理解它天然适合回答总结性/全局性提问。
OVERVIEW_SECTION_PATH = "文档概览"

# 概览块用的 chunk_index。用负数让它排在所有正文块之前，且可被稳定识别。
OVERVIEW_CHUNK_INDEX = -1

_ABSTRACT_HEADING_RE = re.compile(r"^(?:abstract|summary|摘要|概要|概述)\b", re.IGNORECASE)


class SemanticChunker:
    """按语义单元切块，并额外产出一个「文档概览块」。

    概览块解决的是**全局型提问**（「这篇论文提出了什么方法」「主要贡献是什么」）：
    这类问题的答案分散在标题/摘要/引言里，而相似度检索只会命中「话题最相关」的正文块，
    实测会把致谢与参考文献顶到前面、把标题+摘要挤出 top-k。概览块把标题与摘要
    浓缩成一段短文本并单独参与索引，让全局型提问有可命中的对象。
    """

    def __init__(
        self,
        max_tokens: int = 800,
        overlap_tokens: int = 100,
        *,
        include_overview: bool = True,
        overview_max_chars: int = 1200,
        overview_min_chars: int = 400,
    ) -> None:
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.include_overview = include_overview
        self.overview_max_chars = overview_max_chars
        self.overview_min_chars = overview_min_chars
        self.counter = ApproxTokenCounter()

    def chunk(self, document: ParsedDocument) -> list[DocumentChunkDraft]:
        chunks: list[DocumentChunkDraft] = []
        for section in document.sections:
            for text in self._split_section(section):
                if not text.strip():
                    continue
                chunks.append(
                    DocumentChunkDraft(
                        text=text,
                        section_path=" > ".join(section.heading_path),
                        page_number=section.page_number,
                        chunk_index=len(chunks),
                        token_count=self.counter.count(text),
                        source_url=document.source_url,
                    )
                )
        if not self.include_overview:
            return chunks
        overview = self._overview_draft(document)
        if overview is not None:
            chunks.insert(0, overview)
        return chunks

    def _overview_draft(self, document: ParsedDocument) -> DocumentChunkDraft | None:
        text = self._overview_text(document)
        if not text:
            return None
        return DocumentChunkDraft(
            text=text,
            section_path=OVERVIEW_SECTION_PATH,
            page_number=None,
            chunk_index=OVERVIEW_CHUNK_INDEX,
            token_count=self.counter.count(text),
            source_url=document.source_url,
        )

    def _overview_text(self, document: ParsedDocument) -> str:
        body = document.markdown.strip()
        # 短文整体就是一个概览，再造一块只会重复索引同样内容。
        if len(body) <= self.overview_min_chars:
            return ""
        parts: list[str] = []
        title = document.title.strip()
        if title:
            parts.append(f"标题：{title}")
        abstract = self._abstract_text(document)
        if abstract:
            parts.append(" ".join(abstract.split())[: self.overview_max_chars])
        return "\n".join(part for part in parts if part.strip())

    def _abstract_text(self, document: ParsedDocument) -> str:
        for section in document.sections:
            heading = " > ".join(section.heading_path).strip()
            if heading and _ABSTRACT_HEADING_RE.match(heading):
                return section.markdown.strip()
            stripped = section.markdown.strip()
            if stripped and _ABSTRACT_HEADING_RE.match(stripped):
                return stripped
        # 没有显式摘要（常见于 PDF 直接抽取的扁平结构）：退化为文档开头，
        # 实测开头一段已包含方法与贡献的表述。
        if document.sections:
            return document.sections[0].markdown.strip()
        return document.markdown.strip()

    def _split_section(self, section: ParsedSection) -> list[str]:
        text = section.markdown.strip()
        if not text:
            return []
        if self.counter.count(text) <= self.max_tokens:
            return [text]
        units = self._semantic_units(text)
        if not units:
            return []
        if len(units) == 1:
            return self._split_oversized_text(units[0])

        result: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for unit in units:
            unit_tokens = self.counter.count(unit)
            if unit_tokens > self.max_tokens:
                if current:
                    result.append("\n\n".join(current))
                    current, current_tokens = [], 0
                result.extend(self._split_oversized_text(unit))
                continue
            if current and current_tokens + unit_tokens > self.max_tokens:
                result.append("\n\n".join(current))
                overlap_size = min(self.overlap_tokens, self.max_tokens - unit_tokens)
                overlap = self._tail_tokens("\n\n".join(current), overlap_size)
                current = [overlap] if overlap else []
                current_tokens = self.counter.count(overlap)
            current.append(unit)
            current_tokens += unit_tokens
        if current:
            result.append("\n\n".join(current))
        return result

    def _split_oversized_text(self, text: str) -> list[str]:
        if fenced_chunks := self._split_fenced_code(text):
            return fenced_chunks
        tokens = self._token_units(text)
        result: list[str] = []
        start = 0
        while start < len(tokens):
            end = min(start + self.max_tokens, len(tokens))
            result.append(self._join_units(tokens[start:end]))
            if end == len(tokens):
                break
            start = end - self.overlap_tokens
        return result

    def _split_fenced_code(self, text: str) -> list[str] | None:
        match = re.fullmatch(r"(?P<open>`{3,}|~{3,})(?P<language>[^\n]*)\n(?P<body>.*)\n(?P<close>`{3,}|~{3,})", text, re.DOTALL)
        if not match or match.group("open")[0] != match.group("close")[0]:
            return None
        opening = f"{match.group('open')}{match.group('language')}"
        closing = match.group("close")
        capacity = self.max_tokens - self.counter.count(f"{opening}\n{closing}")
        if capacity <= 0:
            return None
        result: list[str] = []
        lines: list[str] = []
        token_count = 0
        for line in match.group("body").splitlines():
            line_tokens = self.counter.count(line)
            if line_tokens > capacity:
                if lines:
                    result.append(opening + "\n" + "\n".join(lines) + "\n" + closing)
                    lines, token_count = [], 0
                result.extend(
                    opening + "\n" + fragment + "\n" + closing
                    for fragment in self._split_oversized_code_line(line, capacity)
                )
                continue
            if lines and token_count + line_tokens > capacity:
                result.append(opening + "\n" + "\n".join(lines) + "\n" + closing)
                lines, token_count = [], 0
            lines.append(line)
            token_count += line_tokens
        if lines:
            result.append(opening + "\n" + "\n".join(lines) + "\n" + closing)
        return result

    @staticmethod
    def _split_oversized_code_line(line: str, capacity: int) -> list[str]:
        indentation = line[: len(line) - len(line.lstrip(" \t"))]
        content = line[len(indentation) :]
        tokens = list(re.finditer(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s\w]", content))
        result: list[str] = []
        start_char = 0
        for token_start in range(0, len(tokens), capacity):
            token_end = min(token_start + capacity, len(tokens))
            end_char = len(content) if token_end == len(tokens) else tokens[token_end - 1].end()
            fragment = content[start_char:end_char].lstrip(" \t")
            result.append(indentation + fragment)
            start_char = end_char
        return result

    @staticmethod
    def _semantic_units(text: str) -> list[str]:
        parts = re.split(r"(^```.*?^```\s*$|^~~~.*?^~~~\s*$)", text, flags=re.MULTILINE | re.DOTALL)
        units: list[str] = []
        for part in parts:
            if not part.strip():
                continue
            if part.lstrip().startswith(("```", "~~~")):
                units.append(part.strip())
            else:
                units.extend(paragraph.strip() for paragraph in re.split(r"\n\s*\n", part) if paragraph.strip())
        return units

    def _tail_tokens(self, text: str, amount: int) -> str:
        tokens = self._token_units(text)
        return self._join_units(tokens[-amount:])

    @staticmethod
    def _token_units(text: str) -> list[str]:
        return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s\w]", text)

    @staticmethod
    def _join_units(units: list[str]) -> str:
        return " ".join(units)
