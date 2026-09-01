from __future__ import annotations

import asyncio
import hashlib
import math
import re
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


class BGEEmbeddingProvider:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings
        self._model: object | None = None
        self._status = ModelStatus(
            state="unavailable", model_name=settings.model_name, message="模型尚未准备"
        )
        self._lock = asyncio.Lock()

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
                message="嵌入模型已准备就绪",
                progress=100,
            )
            return self.status

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text])
        return vectors[0]

    def _build_model(self) -> object:
        # sentence-transformers is intentionally imported only after explicit preparation.
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(
            self.settings.model_name,
            device=self.settings.device,
            cache_folder=str(self.settings.cache_dir) if self.settings.cache_dir else None,
        )

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            raise DomainError(
                "EMBEDDING_UNAVAILABLE", "嵌入模型尚未准备", 503, True, "准备嵌入模型后重试"
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
    return BGEEmbeddingProvider(settings)
