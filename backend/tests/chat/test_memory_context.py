import pytest
from pydantic import TypeAdapter, ValidationError

from app.chat.citations import parse_citations
from app.chat.service import _memory_source_map
from app.memory.retriever import MemoryHit
from app.schemas.chat import Citation


def test_memory_sources_use_m_prefix():
    hit = MemoryHit("v", "distillation", "d", "remember", "repo-a", .9, {})
    sources = _memory_source_map([hit])
    assert list(sources) == ["M1"]
    assert sources["M1"].source_id == "M1"


def test_memory_citation_is_preserved_by_final_parser():
    hit = MemoryHit("v", "distillation", "d", "remember", "repo-a", .9, {})
    sources = _memory_source_map([hit])
    assert [item.source_id for item in parse_citations("answer [M1]", sources)] == ["M1"]


def test_memory_citation_requires_memory_discriminator_fields():
    with pytest.raises(ValidationError):
        TypeAdapter(Citation).validate_python({"kind": "memory", "sourceId": "M1", "title": "x", "excerpt": "x"})
