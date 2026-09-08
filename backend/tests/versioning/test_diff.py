from __future__ import annotations

from app.versioning.diff import diff_versions


def test_diff_marks_added_and_removed_lines() -> None:
    result = diff_versions("a\nb\nc", "a\nB\nc\nD")

    assert [(line.kind, line.text) for line in result] == [
        ("context", "a"),
        ("removed", "b"),
        ("added", "B"),
        ("context", "c"),
        ("added", "D"),
    ]
