from __future__ import annotations

import re

from app.schemas.imports import DocumentChunkDraft, ParsedDocument, ParsedSection


class ApproxTokenCounter:
    def count(self, text: str) -> int:
        cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        non_cjk_words = len(re.findall(r"[A-Za-z0-9_]+|[^\s\w]", text))
        return cjk + non_cjk_words


class SemanticChunker:
    def __init__(self, max_tokens: int = 800, overlap_tokens: int = 100) -> None:
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
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
        return chunks

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
