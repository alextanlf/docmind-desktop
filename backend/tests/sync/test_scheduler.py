from __future__ import annotations

import pytest

from app.sync.scheduler import SyncScheduler


class FakeRepo:
    def __init__(self, repository_id: str, yuque_id: str | None) -> None:
        self.id = repository_id
        self.yuque_id = yuque_id


class FakeRepoStore:
    def __init__(self, repositories: list[FakeRepo]) -> None:
        self.repositories = repositories

    def list(self) -> list[FakeRepo]:
        return self.repositories


class FakeSyncService:
    def __init__(self) -> None:
        self.synced: list[str] = []

    async def sync_repository(self, repository_id: str) -> None:
        self.synced.append(repository_id)


@pytest.mark.asyncio
async def test_scheduler_syncs_bound_repositories_once_without_timer() -> None:
    service = FakeSyncService()
    store = FakeRepoStore([FakeRepo("r1", "yuque-1"), FakeRepo("r2", None)])

    await SyncScheduler(service, store, interval_seconds=0).run()

    assert service.synced == ["r1"]


@pytest.mark.asyncio
async def test_scheduler_stop_sets_stopped_event() -> None:
    service = FakeSyncService()
    scheduler = SyncScheduler(service, FakeRepoStore([]), interval_seconds=0)
    scheduler.stop()
    assert scheduler._stopped.is_set()
