from __future__ import annotations

from typing import Protocol

from app.schemas.web_search import SearchRequest, SearchResponse


class SearchProviderError(RuntimeError):
    """A provider-level failure that should let the chain move to the next source."""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class SearchProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def search(self, request: SearchRequest) -> SearchResponse: ...


def provider_available(provider: object) -> bool:
    checker = getattr(provider, "available", None)
    if checker is None:
        return True
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001 - availability checks must never raise
        return False
