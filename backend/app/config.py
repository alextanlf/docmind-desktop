from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    session_token: SecretStr
    host: str = "127.0.0.1"
    port: int = 18900
    data_dir: Path = Path.home() / ".docmind"
    environment: Literal["development", "test", "production"] = "development"

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


def get_settings() -> AppSettings:
    return AppSettings()
