from __future__ import annotations

from app.core.multilingual import (
    detect_language,
    multilingual_chunks,
    retrieval_threshold,
)


def test_detect_language_english_chinese_other() -> None:
    assert detect_language("state management in swiftui") == "en"
    assert detect_language("状态管理在 SwiftUI 中") == "zh"
    assert detect_language("12345 !!!") == "other"


def test_multilingual_chunks_preserves_sentence_boundaries() -> None:
    assert multilingual_chunks("One. Two. Three.", "en") == ["One.", "Two.", "Three."]


def test_retrieval_threshold_differs_by_language() -> None:
    assert retrieval_threshold("zh") != retrieval_threshold("en")
    assert 0.0 < retrieval_threshold("en") <= 1.0
