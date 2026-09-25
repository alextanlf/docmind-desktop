from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
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
        startup_delay_seconds: float = 0.0,
    ) -> None:
        self.service = service
        self.repository_store = repository_store
        self.interval_seconds = interval_seconds
        self.startup_delay_seconds = startup_delay_seconds
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        # 语雀浏览器上下文是串行资源：启动同步先把锁抢走的话，界面"检查首次设置"
        # 就得排队等一整轮同步（网络异常时可长达数十秒）。因此让交互式检查先跑。
        if self.startup_delay_seconds > 0:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopped.wait(), self.startup_delay_seconds)
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
