from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import AppSettings, EmbeddingSettings
from app.core.embedding import (
    BGEEmbeddingProvider,
    FakeEmbeddingProvider,
    create_embedding_provider,
)


@pytest.fixture
def fake_embedding() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(EmbeddingSettings(dimension=8))


async def test_fake_embedding_is_deterministic(fake_embedding: FakeEmbeddingProvider) -> None:
    first = await fake_embedding.embed_query("@State 管理状态")
    second = await fake_embedding.embed_query("@State 管理状态")

    assert first == second
    assert len(first) == 8


def test_bge_m3_provider_reports_1024_dimension() -> None:
    provider = create_embedding_provider(EmbeddingSettings(model_name="BAAI/bge-m3"))
    assert provider.dimension == 1024


async def test_fake_embedding_scores_shared_state_token_above_retrieval_threshold() -> None:
    provider = FakeEmbeddingProvider(EmbeddingSettings(dimension=768))

    query = await provider.embed_query("@State 有什么作用？")
    document = (await provider.embed_documents(["# 状态管理\n## @State\n@State 管理视图拥有的状态。"]))[0]

    assert sum(left * right for left, right in zip(query, document, strict=True)) >= 0.65


def test_embedding_defaults_use_bge_base_zh_dimension(tmp_path) -> None:
    app_settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)

    assert EmbeddingSettings().model_name == "BAAI/bge-base-zh-v1.5"
    assert EmbeddingSettings().dimension == 768
    assert app_settings.embedding_model_name == "BAAI/bge-base-zh-v1.5"
    assert app_settings.embedding_dimension == 768
    assert app_settings.embedding_settings.dimension == 768


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
