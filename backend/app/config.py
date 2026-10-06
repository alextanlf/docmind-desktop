from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseModel):
    model_name: str = "BAAI/bge-m3"
    device: str = "cpu"
    cache_dir: Path | None = None
    onnx_dir: Path | None = None
    # 随应用分发的内置模型目录（Resources/models，只读）；存在时优先于 onnx_dir。
    bundled_onnx_dir: Path | None = None
    dimension: int = Field(default=1024, gt=0)


class VectorStoreSettings(BaseModel):
    directory: Path
    # sqlite-vec 建表时维度必须写死，运行时无法 ALTER。取自 embedding_dimension，
    # 让换模型时索引重建能被显式触发（见 core/query_intent 之外的 embedding_rebuild 表）。
    dimension: int = Field(default=1024, gt=0)


class AppSettings(BaseSettings):
    session_token: SecretStr
    host: str = "127.0.0.1"
    port: int = 18900
    data_dir: Path = Path.home() / ".docmind"
    environment: Literal["development", "test", "production"] = "development"

    max_source_redirects: int = 5
    source_connect_timeout_seconds: float = 10.0
    source_read_timeout_seconds: float = 30.0
    html_markdown_max_bytes: int = 20 * 1024 * 1024
    pdf_max_bytes: int = 100 * 1024 * 1024
    max_request_body_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"
    embedding_dimension: int = Field(default=1024, gt=0)
    # 随应用分发的内置模型根目录（Electron 打包态注入 DOCMIND_BUNDLED_MODELS_DIR），
    # 对应 .app/Contents/Resources/models，只读。
    bundled_models_dir: Path | None = None
    # 送进 LLM 的检索块数上限。召回候选池(见 HybridRetriever.candidate_pool)可以很大，
    # 但提示词长度与成本随块数线性增长，所以最终结果在这里封顶。
    rag_max_sources: int = Field(default=5, ge=1, le=20)
    # 记忆召回的相似度下限。**只作用于记忆召回**，检索侧刻意不设阈值门控：
    # 固定绝对余弦分数随查询长度漂移（实测 0.59~0.99），任何阈值都会错杀短查询。
    # 详见 memory/retriever.py 的过滤条件。
    memory_recall_min_similarity: float = Field(default=0.65, ge=-1.0, le=1.0)
    staging_manifest_max_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    batch_max_items: int = Field(default=1000, gt=0)
    sync_interval_seconds: float = Field(default=0.0, ge=0.0)

    model_config = SettingsConfigDict(env_prefix="DOCMIND_", extra="ignore")

    @model_validator(mode="after")
    def create_data_directories(self) -> AppSettings:
        directories = (
            self.data_dir,
            self.data_dir / "database",
            self.data_dir / "vectorstore",
            self.data_dir / "documents",
            self.data_dir / "imports" / "staging",
            self.data_dir / "browser-data",
            self.data_dir / "logs" / "screenshots",
        )
        for directory in directories:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                directory.chmod(0o700)
        return self

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "logs" / "screenshots"

    @property
    def browser_data_dir(self) -> Path:
        return self.data_dir / "browser-data"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "imports" / "staging"

    @property
    def vectorstore_dir(self) -> Path:
        return self.data_dir / "vectorstore"

    @property
    def embedding_settings(self) -> EmbeddingSettings:
        model_slug = self.embedding_model_name.replace("/", "--")
        return EmbeddingSettings(
            model_name=self.embedding_model_name,
            device=self.embedding_device,
            cache_dir=self.data_dir / "models",
            onnx_dir=self.data_dir / "models" / f"onnx--{model_slug}",
            bundled_onnx_dir=(
                self.bundled_models_dir / f"onnx--{model_slug}"
                if self.bundled_models_dir is not None
                else None
            ),
            dimension=self.embedding_dimension,
        )

    @property
    def vectorstore_settings(self) -> VectorStoreSettings:
        return VectorStoreSettings(
            directory=self.vectorstore_dir, dimension=self.embedding_dimension
        )


def get_settings() -> AppSettings:
    return AppSettings()
