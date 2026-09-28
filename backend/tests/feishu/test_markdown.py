from __future__ import annotations

from app.feishu.markdown import render_document_blocks


def _text(content: str, **style: object) -> dict:
    return {
        "elements": [
            {
                "text_run": {
                    "content": content,
                    "text_element_style": style,
                }
            }
        ]
    }


def test_render_document_blocks_reconstructs_common_markdown() -> None:
    blocks = [
        {"block_id": "page", "block_type": 1, "children": ["h1", "p", "list", "code", "quote", "todo", "divider"]},
        {"block_id": "h1", "parent_id": "page", "block_type": 3, "heading1": _text("标题")},
        {"block_id": "p", "parent_id": "page", "block_type": 2, "text": _text("正文")},
        {
            "block_id": "list",
            "parent_id": "page",
            "block_type": 12,
            "bullet": _text("第一项"),
            "children": ["nested"],
        },
        {"block_id": "nested", "parent_id": "list", "block_type": 13, "ordered": _text("子项")},
        {"block_id": "code", "parent_id": "page", "block_type": 14, "code": _text("print(1)\n")},
        {"block_id": "quote", "parent_id": "page", "block_type": 15, "quote": _text("引用")},
        {
            "block_id": "todo",
            "parent_id": "page",
            "block_type": 17,
            "todo": {"style": {"done": True}, **_text("完成")},
        },
        {"block_id": "divider", "parent_id": "page", "block_type": 22, "divider": {}},
    ]

    rendered = render_document_blocks(blocks)

    assert "# 标题" in rendered
    assert "正文" in rendered
    assert "- 第一项\n  1. 子项" in rendered
    assert "```\nprint(1)\n```" in rendered
    assert "> 引用" in rendered
    assert "- [x] 完成" in rendered
    assert "\n---" in rendered


def test_render_document_blocks_preserves_inline_styles_links_and_tables() -> None:
    blocks = [
        {"block_id": "page", "block_type": 1, "children": ["p", "table"]},
        {
            "block_id": "p",
            "parent_id": "page",
            "block_type": 2,
            "text": {
                "elements": [
                    {"text_run": {"content": "粗体", "text_element_style": {"bold": True}}},
                    {"text_run": {"content": "链接", "text_element_style": {"link": {"url": "https://example.test"}}}},
                    {"text_run": {"content": "code", "text_element_style": {"inline_code": True}}},
                    {"equation": {"content": "x^2"}},
                ]
            },
        },
        {
            "block_id": "table",
            "parent_id": "page",
            "block_type": 31,
            "table": {
                "cells": ["c1", "c2", "c3", "c4"],
                "property": {"row_size": 2, "column_size": 2},
            },
        },
        {"block_id": "c1", "parent_id": "table", "block_type": 32, "children": ["c1p"]},
        {"block_id": "c1p", "parent_id": "c1", "block_type": 2, "text": _text("标题 A")},
        {"block_id": "c2", "parent_id": "table", "block_type": 32, "children": ["c2p"]},
        {"block_id": "c2p", "parent_id": "c2", "block_type": 2, "text": _text("标题 B")},
        {"block_id": "c3", "parent_id": "table", "block_type": 32, "children": ["c3p"]},
        {"block_id": "c3p", "parent_id": "c3", "block_type": 2, "text": _text("值 | A")},
        {"block_id": "c4", "parent_id": "table", "block_type": 32, "children": ["c4p"]},
        {"block_id": "c4p", "parent_id": "c4", "block_type": 2, "text": _text("值 B")},
    ]

    rendered = render_document_blocks(blocks)

    assert "**粗体**" in rendered
    assert "[链接](https://example.test)" in rendered
    assert "`code`" in rendered
    assert "$x^2$" in rendered
    assert "| 标题 A | 标题 B |" in rendered
    assert r"值 \| A" in rendered
