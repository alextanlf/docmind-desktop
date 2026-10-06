"""Status and model listing for a local inference server.

Deliberately much smaller than the pull-oriented service it replaces: DocMind
does not download models. People who run a local model already installed one
with the tool that runs it, so this boundary answers only two questions — is the
server reachable, and what models does it currently offer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.api.errors import DomainError
from app.core.local_model import (
    MODEL_NOT_FOUND_CODE,
    MODEL_NOT_FOUND_MESSAGE,
    LocalModelProvider,
)
from app.core.local_model_validation import (
    UNAVAILABLE_CODE,
    UNAVAILABLE_MESSAGE,
)
from app.schemas.local_model import (
    DEFAULT_LOCAL_BASE_URL,
    LocalModelConfig,
    LocalModelsView,
    LocalModelView,
    LocalStatusView,
)

HEALTH_CACHE_SECONDS = 5.0

# Error codes that mean "the server is not usable right now" as opposed to
# "it answered but the response was wrong". Used to decide whether the status
# endpoint should report unavailable rather than surfacing a protocol error.
_UNAVAILABLE_CODES = frozenset({UNAVAILABLE_CODE})


@dataclass
class _HealthCache:
    checked_monotonic: float
    models: LocalModelsView
    status: LocalStatusView | None = None


class LocalModelService:
    """Reachable-check and model listing for the configured local server."""

    def __init__(
        self,
        base_url: str | LocalModelConfig | None = None,
        model: str = "",
        *,
        config: LocalModelConfig | None = None,
        provider: LocalModelProvider | None = None,
        clock: Callable[[], float] | None = None,
        base_url_provider: Callable[[], str] | None = None,
        resolver: Any = None,
    ) -> None:
        if config is None and isinstance(base_url, LocalModelConfig):
            config = base_url
            base_url = None
        if config is None:
            config = LocalModelConfig(base_url=base_url or DEFAULT_LOCAL_BASE_URL, model=model)
        self.config = config
        self._base_url = config.base_url.rstrip("/")
        # Re-read on every access so a settings change takes effect without
        # rebuilding this long-lived service. Inference builds its own provider
        # from the same stored config, so a stale address here would report on a
        # different host than the one that serves the actual request.
        self._base_url_provider = base_url_provider
        self._resolved_base_url = self._base_url
        self._health: _HealthCache | None = None
        self._clock = clock or time.monotonic
        self._resolver = resolver

    @property
    def base_url(self) -> str:
        if self._base_url_provider is not None:
            resolved = self._base_url_provider().rstrip("/")
            if resolved:
                # A changed address invalidates the cached probe: it describes
                # the old host and would otherwise mask the new one.
                if resolved != self._resolved_base_url:
                    self._resolved_base_url = resolved
                    self._health = None
                return resolved
        return self._base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        self._base_url = value.rstrip("/")
        self._resolved_base_url = self._base_url

    def invalidate_health(self) -> None:
        self._health = None

    def _cache_is_fresh(self) -> bool:
        # Touch base_url first so a settings-driven change clears the cache
        # before we decide the cached answer still describes the current host.
        _ = self.base_url
        return (
            self._health is not None
            and self._clock() - self._health.checked_monotonic < HEALTH_CACHE_SECONDS
        )

    def _provider(self) -> LocalModelProvider:
        return LocalModelProvider(config=self.config.model_copy(update={"base_url": self.base_url}))

    async def models(self) -> LocalModelsView:
        if self._cache_is_fresh() and self._health is not None:
            return self._health.models
        checked_at = datetime.now(UTC)
        try:
            ids = await self._provider().list_models()
        except DomainError as error:
            if error.code not in _UNAVAILABLE_CODES:
                # A protocol or auth problem is a real fault; the caller should
                # see it rather than a generic "not running".
                raise
            view = LocalModelsView(
                available=False, models=[], checked_at=checked_at, message=UNAVAILABLE_MESSAGE
            )
        else:
            view = LocalModelsView(
                available=True,
                models=[LocalModelView(id=model_id, label=model_id) for model_id in ids],
                checked_at=checked_at,
                message="本地模型服务已连接",
            )
        self._health = _HealthCache(self._clock(), view, None)
        return view

    async def status(self) -> LocalStatusView:
        if self._cache_is_fresh() and self._health is not None and self._health.status is not None:
            return self._health.status
        models = await self.models()
        status = LocalStatusView(
            available=models.available,
            base_url=self.base_url,
            version=None,
            selected_model=self.config.model,
            selected_model_available=models.available
            and self.config.model in {item.id for item in models.models},
            checked_at=models.checked_at,
            message=models.message,
        )
        if self._health is None:
            self._health = _HealthCache(self._clock(), models, status)
        else:
            self._health.status = status
        return status

    async def is_model_installed(self, model_name: str | None = None) -> bool:
        selected = model_name if model_name is not None else self.config.model
        if not selected:
            return False
        models = await self.models()
        return models.available and selected in {item.id for item in models.models}

    async def preflight_model(self, model_name: str) -> None:
        if not model_name:
            raise DomainError(MODEL_NOT_FOUND_CODE, MODEL_NOT_FOUND_MESSAGE, 503, True)
        if not await self.is_model_installed(model_name):
            raise DomainError(MODEL_NOT_FOUND_CODE, MODEL_NOT_FOUND_MESSAGE, 503, True)
