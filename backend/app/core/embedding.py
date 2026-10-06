from __future__ import annotations

import asyncio
import hashlib
import math
import re
from pathlib import Path
from typing import Protocol

from app.api.errors import DomainError
from app.config import EmbeddingSettings
from app.schemas.embedding import ModelStatus


class EmbeddingProvider(Protocol):
    @property
    def status(self) -> ModelStatus: ...

    async def ensure_ready(self) -> ModelStatus: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


async def require_ready_embedding(provider: EmbeddingProvider) -> ModelStatus:
    """确保嵌入模型可用，否则抛 503。

    🔴 嵌入是**索引期硬依赖**（没有向量就写不了索引），不是可降级项 ——
    所以这里是抛错而不是回退。此前这段 `ensure_ready()` + `state != "ready"`
    + `DomainError("INDEX_FAILED", "嵌入模型不可用", 503, True)` 在
    `api/documents.py`（两处）、`imports/service.py`、`sync/refresher.py`
    各抄了一份，四份的错误码与文案靠人工保持一致。收敛到这里。
    """
    status = await provider.ensure_ready()
    if status.state != "ready":
        raise DomainError("INDEX_FAILED", "嵌入模型不可用", 503, True)
    return status


class BGEEmbeddingProvider:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings
        self._model: object | None = None
        self._quantized_model = self._onnx_model_path() is not None
        self._cached_model = self._quantized_model or self._cache_root().is_dir()
        self._status = ModelStatus(
            state="unavailable",
            model_name=settings.model_name,
            # 这是应用刚启动、lifespan 预热任务尚未完成的瞬间状态。
            # 措辞刻意中性：既不说「下载」（模型随应用分发，说下载会让人以为要额外下
            # 几百 MB），也不说「点击加载」（启动即自动预热，没有手动动作）。
            message="模型已内置，正在加载" if self._cached_model else "模型尚未准备",
        )
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        return self.settings.dimension

    @property
    def status(self) -> ModelStatus:
        return self._status.model_copy()

    async def ensure_ready(self) -> ModelStatus:
        if self._model is not None:
            return self.status
        async with self._lock:
            if self._model is not None:
                return self.status
            self._status = ModelStatus(
                state="downloading", model_name=self.settings.model_name, message="正在准备嵌入模型", progress=0
            )
            try:
                model = await asyncio.to_thread(self._build_model)
                vector = await asyncio.to_thread(self._encode, model, ["dimension probe"])
                self._validate_vectors(vector)
            except Exception as error:
                self._status = ModelStatus(
                    state="error", model_name=self.settings.model_name, message="嵌入模型准备失败"
                )
                if isinstance(error, ValueError):
                    raise
                return self.status
            self._model = model
            self._status = ModelStatus(
                state="ready",
                model_name=self.settings.model_name,
                dimension=self.settings.dimension,
                message="嵌入模型已就绪（ONNX int8 量化版）"
                if self._quantized_model
                else "嵌入模型已准备就绪",
                progress=100,
            )
            return self.status

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text])
        return vectors[0]

    def _build_model(self) -> object:
        # 本地已有 int8 量化 ONNX 版模型时优先使用（体积约为 fp32 的 1/4，CPU 推理更快），
        # 否则回退到 sentence-transformers 的 fp32 路径。
        # sentence-transformers / onnxruntime 均刻意只在显式准备后才导入。
        onnx_model_path = self._onnx_model_path()
        if onnx_model_path is not None:
            from app.core.onnx_embedding import ONNXEmbeddingModel

            return ONNXEmbeddingModel(onnx_model_path.parent, max_seq_length=8192)

        # 打包态必然内置/可下载 int8 ONNX 模型，走不到这里；sentence-transformers
        # 只装在可选依赖 fp32 里（它会拖入约 500MB 的 torch），缺失时给出可操作提示
        # 而不是裸 ImportError。
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise ValueError(
                "未找到 int8 ONNX 模型，且未安装 fp32 回退依赖。"
                "请在设置中准备嵌入模型，或执行 `uv sync --extra fp32` 安装 "
                "sentence-transformers。"
            ) from error

        return SentenceTransformer(
            self.settings.model_name,
            device=self.settings.device,
            cache_folder=str(self.settings.cache_dir) if self.settings.cache_dir else None,
            local_files_only=self._cached_model,
        )

    def _onnx_model_path(self) -> Path | None:
        # 解析顺序：随应用分发的内置模型（Resources/models，只读，版本与打包应用绑定）
        # 优先于用户数据目录中显式准备的模型（开发态 / 未内置分发的安装的兜底）。
        for directory in (self.settings.bundled_onnx_dir, self.settings.onnx_dir):
            if directory is None:
                continue
            onnx_path = directory / "model.onnx"
            if onnx_path.is_file():
                return onnx_path
        return None

    def _cache_root(self) -> Path:
        if self.settings.cache_dir is None:
            return Path("__missing_docmind_cache__")
        return self.settings.cache_dir / f"models--{self.settings.model_name.replace('/', '--')}"

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            raise DomainError(
                "EMBEDDING_UNAVAILABLE", "嵌入模型尚未准备", 503, True, "稍后重试"
            )
        vectors = await asyncio.to_thread(self._encode, self._model, texts)
        self._validate_vectors(vectors)
        return vectors

    @staticmethod
    def _encode(model: object, texts: list[str]) -> list[list[float]]:
        vectors = model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [list(map(float, vector)) for vector in vectors]

    def _validate_vectors(self, vectors: list[list[float]]) -> None:
        if any(len(vector) != self.settings.dimension for vector in vectors):
            raise ValueError(
                f"embedding dimension does not match configured dimension {self.settings.dimension}"
            )


class FakeEmbeddingProvider:
    """Deterministic, local-only provider used by tests and development fixtures."""

    def __init__(self, settings: EmbeddingSettings, preparation_delay: bool = False) -> None:
        self.settings = settings
        self.preparation_delay = preparation_delay
        self.ensure_ready_calls = 0
        self._status = ModelStatus(
            state="unavailable", model_name=settings.model_name, message="模型尚未准备"
        )

    @property
    def status(self) -> ModelStatus:
        return self._status.model_copy()

    async def ensure_ready(self) -> ModelStatus:
        self.ensure_ready_calls += 1
        self._status = ModelStatus(
            state="downloading", model_name=self.settings.model_name, message="正在准备嵌入模型", progress=0
        )
        if self.preparation_delay:
            await asyncio.sleep(0.1)
        self._status = ModelStatus(
            state="ready",
            model_name=self.settings.model_name,
            dimension=self.settings.dimension,
            message="嵌入模型已准备就绪",
            progress=100,
        )
        return self.status

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        buckets = [0.0] * self.settings.dimension
        for token in re.findall(r"@[A-Za-z0-9_]+|[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=2).digest()
            weight = 12.0 if token.startswith("@") else 1.0
            buckets[int.from_bytes(digest, "big") % self.settings.dimension] += weight
        length = math.sqrt(sum(value * value for value in buckets))
        return [value / length for value in buckets] if length else buckets


def create_embedding_provider(settings: EmbeddingSettings) -> EmbeddingProvider:
    if "bge-m3" in settings.model_name and settings.dimension == 768:
        settings = settings.model_copy(update={"dimension": 1024})
    return BGEEmbeddingProvider(settings)
