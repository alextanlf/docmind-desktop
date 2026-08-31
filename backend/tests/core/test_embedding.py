from __future__ import annotations

import pytest

from app.config import EmbeddingSettings
from app.core.embedding import BGEEmbeddingProvider, FakeEmbeddingProvider


@pytest.fixture
def fake_embedding() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(EmbeddingSettings(dimension=8))


async def test_fake_embedding_is_deterministic(fake_embedding: FakeEmbeddingProvider) -> None:
    first = await fake_embedding.embed_query("@State 管理状态")
    second = await fake_embedding.embed_query("@State 管理状态")

    assert first == second
    assert len(first) == 8


async def test_bge_provider_defers_model_import_until_explicit_preparation(monkeypatch) -> None:
    provider = BGEEmbeddingProvider(EmbeddingSettings(dimension=3))
    imported = False

    def build_model() -> object:
        nonlocal imported
        imported = True
        return _Model([[0.0, 0.0, 1.0]])

    monkeypatch.setattr(provider, "_build_model", build_model)

    assert provider.status.state == "unavailable"
    assert imported is False
    await provider.ensure_ready()

    assert imported is True
    assert provider.status.state == "ready"
    assert await provider.embed_query("query") == [0.0, 0.0, 1.0]


async def test_bge_provider_rejects_unexpected_embedding_dimension(monkeypatch) -> None:
    provider = BGEEmbeddingProvider(EmbeddingSettings(dimension=3))
    monkeypatch.setattr(provider, "_build_model", lambda: _Model([[1.0, 0.0]]))

    with pytest.raises(ValueError, match="dimension"):
        await provider.ensure_ready()

    assert provider.status.state == "error"


class _Model:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors

    def encode(self, texts: list[str], normalize_embeddings: bool) -> list[list[float]]:
        assert normalize_embeddings is True
        return self.vectors * len(texts)
