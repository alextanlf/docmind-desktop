from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Runs startup and periodic per-repository sync."""

    def __init__(
        self,
        service: Any,
        repository_store: Any,
        *,
        interval_seconds: float = 0.0,
    ) -> None:
        self.service = service
        self.repository_store = repository_store
        self.interval_seconds = interval_seconds
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        while not self._stopped.is_set():
            for repository in self.repository_store.list():
                if self._stopped.is_set():
                    return
                if not repository.yuque_id:
                    continue
                try:
                    await self.service.sync_repository(repository.id)
                except Exception:
                    logger.exception("sync failed for repository %s", repository.id)
            if self.interval_seconds <= 0:
                return
            try:
                await asyncio.wait_for(self._stopped.wait(), self.interval_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()
