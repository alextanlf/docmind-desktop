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
    dimension: int = Field(default=1024, gt=0)


class VectorStoreSettings(BaseModel):
    directory: Path


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
    rag_similarity_threshold: float = Field(default=0.65, ge=-1.0, le=1.0)
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
        return EmbeddingSettings(
            model_name=self.embedding_model_name,
            device=self.embedding_device,
            cache_dir=self.data_dir / "models",
            dimension=self.embedding_dimension,
        )

    @property
    def vectorstore_settings(self) -> VectorStoreSettings:
        return VectorStoreSettings(directory=self.vectorstore_dir)


def get_settings() -> AppSettings:
    return AppSettings()
