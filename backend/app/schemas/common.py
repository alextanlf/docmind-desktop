from __future__ import annotations

from pydantic import BaseModel, ConfigDict


def to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ErrorBody(WireModel):
    code: str
    message: str
    retryable: bool = False
    action: str | None = None


class ErrorEnvelope(WireModel):
    error: ErrorBody


class HealthResponse(WireModel):
    status: str
    version: str
