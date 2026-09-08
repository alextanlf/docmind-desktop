from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def detect_language(text: str) -> str:
    if not text:
        return "other"
    total = len(text)
    cjk_ratio = len(_CJK_RE.findall(text)) / total
    ascii_ratio = len(_ASCII_LETTER_RE.findall(text)) / total
    if cjk_ratio >= 0.2:
        return "zh"
    if ascii_ratio >= 0.5:
        return "en"
    return "other"


def multilingual_chunks(text: str, language: str) -> list[str]:
    del language
    chunks = [chunk for chunk in _SENTENCE_SPLIT_RE.split(text) if chunk.strip()]
    return chunks or [text]


def retrieval_threshold(language: str) -> float:
    if language == "zh":
        return 0.65
    if language == "en":
        return 0.55
    return 0.60
