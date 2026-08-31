from __future__ import annotations

from typing import Literal

from app.schemas.common import WireModel


class ModelStatus(WireModel):
    state: Literal["unavailable", "downloading", "ready", "error"]
    model_name: str
    dimension: int | None = None
    message: str
    progress: int | None = None
