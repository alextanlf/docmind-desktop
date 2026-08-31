from __future__ import annotations

from app.document.chunker import ApproxTokenCounter, SemanticChunker
from app.schemas.imports import ParsedDocument, ParsedSection


def test_token_counter_counts_cjk_words_identifiers_and_punctuation() -> None:
    assert ApproxTokenCounter().count("你好 world_2!") == 4


def test_chunker_preserves_heading_path_code_block_and_stable_metadata() -> None:
    document = ParsedDocument(
        title="State",
        source_url="https://docs.test/state",
        markdown="# 状态管理\n\n## @State\n\n```swift\n@State private var count = 0\n```\n\n说明文字。",
        sections=[
            ParsedSection(
                heading_path=["状态管理", "@State"],
                markdown="```swift\n@State private var count = 0\n```\n\n说明文字。",
            )
        ],
    )
    chunks = SemanticChunker().chunk(document)
    assert len(chunks) == 1
    assert chunks[0].text.startswith("```swift")
    assert chunks[0].section_path == "状态管理 > @State"
    assert (chunks[0].chunk_index, chunks[0].page_number, chunks[0].source_url) == (0, None, "https://docs.test/state")


def test_chunker_splits_oversized_single_unit_with_100_token_overlap_and_keeps_ordering() -> None:
    words = " ".join(f"word{index}" for index in range(950))
    document = ParsedDocument(
        title="Long",
        source_url="https://docs.test/long",
        markdown=words,
        sections=[ParsedSection(heading_path=["Long"], markdown=words, page_number=4), ParsedSection(heading_path=["Empty"], markdown="")],
    )
    chunks = SemanticChunker().chunk(document)
    assert len(chunks) == 2
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert [chunk.page_number for chunk in chunks] == [4, 4]
    assert all(chunk.section_path == "Long" for chunk in chunks)
    assert all(chunk.token_count <= 800 for chunk in chunks)
    assert chunks[0].text.split()[-100:] == chunks[1].text.split()[:100]


def test_chunker_keeps_a_fenced_code_block_intact_when_nearby_prose_requires_a_boundary() -> None:
    prose = " ".join(f"word{index}" for index in range(790))
    code = "```python\nprint('complete block')\n```"
    document = ParsedDocument(
        title="Code",
        source_url="https://docs.test/code",
        markdown=f"{prose}\n\n{code}",
        sections=[ParsedSection(heading_path=["Code"], markdown=f"{prose}\n\n{code}")],
    )
    chunks = SemanticChunker().chunk(document)
    assert len(chunks) == 2
    assert chunks[1].text.endswith(code)
    assert chunks[0].text.split()[-100:] == chunks[1].text.split()[:100]
    assert all(chunk.token_count <= 800 for chunk in chunks)


def test_chunker_drops_empty_sections() -> None:
    document = ParsedDocument(
        title="Empty", source_url="x", markdown="", sections=[ParsedSection(heading_path=[], markdown="\n\t ")]
    )
    assert SemanticChunker().chunk(document) == []
