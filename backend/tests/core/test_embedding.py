from __future__ import annotations

from pathlib import Path

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


def test_embedding_defaults_use_bge_m3_dimension(tmp_path) -> None:
    app_settings = AppSettings(session_token=SecretStr("token"), data_dir=tmp_path)

    assert EmbeddingSettings().model_name == "BAAI/bge-m3"
    assert EmbeddingSettings().dimension == 1024
    assert app_settings.embedding_model_name == "BAAI/bge-m3"
    assert app_settings.embedding_dimension == 1024
    assert app_settings.embedding_settings.dimension == 1024
    assert app_settings.embedding_settings.onnx_dir == (
        tmp_path / "models" / "onnx--BAAI--bge-m3"
    )
    assert app_settings.embedding_settings.bundled_onnx_dir is None


def test_embedding_settings_derive_bundled_onnx_dir(tmp_path) -> None:
    app_settings = AppSettings(
        session_token=SecretStr("token"),
        data_dir=tmp_path / "data",
        bundled_models_dir=tmp_path / "resources" / "models",
    )

    assert app_settings.embedding_settings.bundled_onnx_dir == (
        tmp_path / "resources" / "models" / "onnx--BAAI--bge-m3"
    )
    assert app_settings.embedding_settings.onnx_dir == (
        tmp_path / "data" / "models" / "onnx--BAAI--bge-m3"
    )


def _make_onnx_model_dir(parent: Path) -> Path:
    onnx_dir = parent / "onnx--BAAI--bge-m3"
    onnx_dir.mkdir(parents=True)
    (onnx_dir / "model.onnx").write_bytes(b"stub")
    return onnx_dir


async def test_bge_provider_prefers_bundled_onnx_model(monkeypatch, tmp_path) -> None:
    bundled_dir = _make_onnx_model_dir(tmp_path / "resources" / "models")
    user_dir = _make_onnx_model_dir(tmp_path / "user" / "models")
    settings = EmbeddingSettings(dimension=3, onnx_dir=user_dir, bundled_onnx_dir=bundled_dir)
    provider = BGEEmbeddingProvider(settings)
    assert provider._quantized_model is True
    assert provider._onnx_model_path() == bundled_dir / "model.onnx"

    def fake_onnx_model(model_dir, **kwargs) -> object:
        assert Path(model_dir) == bundled_dir
        return _Model([[0.0, 1.0, 0.0]])

    monkeypatch.setattr("app.core.onnx_embedding.ONNXEmbeddingModel", fake_onnx_model)
    status = await provider.ensure_ready()
    assert status.state == "ready"
    assert status.message == "嵌入模型已就绪（ONNX int8 量化版）"


async def test_bge_provider_falls_back_to_user_onnx_dir_when_bundled_missing(
    monkeypatch, tmp_path
) -> None:
    user_dir = _make_onnx_model_dir(tmp_path / "user" / "models")
    bundled_dir = tmp_path / "resources" / "models" / "onnx--BAAI--bge-m3"
    bundled_dir.mkdir(parents=True)  # 存在但缺 model.onnx
    settings = EmbeddingSettings(dimension=3, onnx_dir=user_dir, bundled_onnx_dir=bundled_dir)
    provider = BGEEmbeddingProvider(settings)

    def fake_onnx_model(model_dir, **kwargs) -> object:
        assert Path(model_dir) == user_dir
        return _Model([[1.0, 0.0, 0.0]])

    monkeypatch.setattr("app.core.onnx_embedding.ONNXEmbeddingModel", fake_onnx_model)
    assert provider._onnx_model_path() == user_dir / "model.onnx"
    status = await provider.ensure_ready()
    assert status.state == "ready"


async def test_bge_provider_prefers_quantized_onnx_model(monkeypatch, tmp_path) -> None:
    onnx_dir = tmp_path / "onnx--BAAI--bge-m3"
    onnx_dir.mkdir()
    (onnx_dir / "model.onnx").write_bytes(b"stub")
    settings = EmbeddingSettings(dimension=3, onnx_dir=onnx_dir)
    provider = BGEEmbeddingProvider(settings)
    assert provider.status.state == "unavailable"
    assert provider.status.message == "模型已缓存，点击加载"

    def fake_onnx_model(model_dir, **kwargs) -> object:
        assert Path(model_dir) == onnx_dir
        return _Model([[0.0, 1.0, 0.0]])

    monkeypatch.setattr("app.core.onnx_embedding.ONNXEmbeddingModel", fake_onnx_model)
    status = await provider.ensure_ready()

    assert status.state == "ready"
    assert status.message == "嵌入模型已就绪（ONNX int8 量化版）"
    assert await provider.embed_query("query") == [0.0, 1.0, 0.0]


def test_bge_provider_ignores_onnx_dir_without_model_file(tmp_path) -> None:
    settings = EmbeddingSettings(dimension=3, onnx_dir=tmp_path / "empty")
    settings.onnx_dir.mkdir()
    provider = BGEEmbeddingProvider(settings)
    assert provider._onnx_model_path() is None
    assert provider._quantized_model is False


async def test_bge_provider_falls_back_to_sentence_transformers(monkeypatch, tmp_path) -> None:
    settings = EmbeddingSettings(dimension=3, onnx_dir=tmp_path / "empty")
    provider = BGEEmbeddingProvider(settings)

    def build_model() -> object:
        return _Model([[1.0, 0.0, 0.0]])

    monkeypatch.setattr(provider, "_build_model", build_model)
    status = await provider.ensure_ready()

    assert status.state == "ready"
    assert status.message == "嵌入模型已准备就绪"


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


def test_onnx_encode_pools_each_batch_before_concatenating() -> None:
    """每个批次各自 padding 到不同序列长度，必须逐批 CLS 池化后再拼接。

    回归：原先直接 concatenate 各批的 (batch, seq, dim) 输出，
    当第二批的 seq 与第一批不同（如 1242 与 264）时会抛
    ValueError: all the input array dimensions except for the concatenation axis must match。
    """
    import numpy as np

    from app.core.onnx_embedding import ONNXEmbeddingModel

    model = object.__new__(ONNXEmbeddingModel)
    model.max_seq_length = 8
    model.batch_size = 2
    model._input_names = {"input_ids", "attention_mask"}
    padded_lengths = iter([5, 3])
    cls_token_id = 7

    class _Tokenizer:
        def __call__(self, batch, **_kwargs):
            length = next(padded_lengths)
            return {"input_ids": np.full((len(batch), length), cls_token_id, dtype=np.int64)}

    class _Session:
        def run(self, _outputs, feed):
            input_ids = feed["input_ids"]
            stacked = np.stack([input_ids * (index + 1) for index in range(3)], axis=-1)
            return [stacked.astype(np.float32)]

    model.tokenizer = _Tokenizer()
    model.session = _Session()

    vectors = model.encode(["a", "b", "c"], normalize_embeddings=False)

    assert vectors.shape == (3, 3)
    assert vectors.tolist() == [[float(cls_token_id * (index + 1)) for index in range(3)]] * 3
