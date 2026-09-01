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


def test_chunker_limits_a_multi_unit_chunk_after_adding_overlap() -> None:
    first = " ".join(f"first{index}" for index in range(750))
    second = " ".join(f"second{index}" for index in range(750))
    document = ParsedDocument(
        title="Paragraphs",
        source_url="https://docs.test/paragraphs",
        markdown=f"{first}\n\n{second}",
        sections=[ParsedSection(heading_path=["Paragraphs"], markdown=f"{first}\n\n{second}")],
    )
    chunks = SemanticChunker().chunk(document)
    assert all(chunk.token_count <= 800 for chunk in chunks)
    assert chunks[0].text.split()[-50:] == chunks[1].text.split()[:50]


def test_chunker_splits_oversized_fenced_code_into_valid_indented_fences() -> None:
    code_lines = [f"    value_{index} = {index}" for index in range(900)]
    code = "```python\n" + "\n".join(code_lines) + "\n```"
    document = ParsedDocument(
        title="Large code",
        source_url="https://docs.test/code",
        markdown=code,
        sections=[ParsedSection(heading_path=["Code"], markdown=code)],
    )
    chunks = SemanticChunker().chunk(document)
    assert len(chunks) > 1
    assert all(chunk.text.startswith("```python\n") and chunk.text.endswith("\n```") for chunk in chunks)
    assert all(line.startswith("    ") for chunk in chunks for line in chunk.text.splitlines()[1:-1])
    assert all(chunk.token_count <= 800 for chunk in chunks)


def test_chunker_keeps_fences_and_indentation_when_one_code_line_is_oversized() -> None:
    long_expression = " + ".join(f"value_{index}" for index in range(40))
    code_line = f"    result = {long_expression}"
    code = f"```python\n{code_line}\n```"
    document = ParsedDocument(
        title="Single long line",
        source_url="https://docs.test/code",
        markdown=code,
        sections=[ParsedSection(heading_path=["Code"], markdown=code)],
    )

    chunks = SemanticChunker(max_tokens=24, overlap_tokens=4).chunk(document)

    assert len(chunks) > 1
    assert all(chunk.text.startswith("```python\n") and chunk.text.endswith("\n```") for chunk in chunks)
    assert all(chunk.text.splitlines()[1].startswith("    ") for chunk in chunks)
    assert all(chunk.token_count <= 24 for chunk in chunks)


def test_chunker_drops_empty_sections() -> None:
    document = ParsedDocument(
        title="Empty", source_url="x", markdown="", sections=[ParsedSection(heading_path=[], markdown="\n\t ")]
    )
    assert SemanticChunker().chunk(document) == []
