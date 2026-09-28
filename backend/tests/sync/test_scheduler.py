from __future__ import annotations

import asyncio

import pytest

from app.sync.scheduler import SyncScheduler


class FakeRepo:
    def __init__(self, repository_id: str, remote_id: str | None) -> None:
        self.id = repository_id
        self.remote_id = remote_id


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

@pytest.mark.asyncio
async def test_scheduler_waits_for_startup_delay_before_syncing() -> None:
    service = FakeSyncService()
    store = FakeRepoStore([FakeRepo("r1", "yuque-1")])

    await SyncScheduler(service, store, interval_seconds=0, startup_delay_seconds=0.05).run()

    assert service.synced == ["r1"]


@pytest.mark.asyncio
async def test_scheduler_stop_interrupts_startup_delay() -> None:
    service = FakeSyncService()
    scheduler = SyncScheduler(
        service,
        FakeRepoStore([FakeRepo("r1", "yuque-1")]),
        interval_seconds=0,
        startup_delay_seconds=30,
    )

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    assert service.synced == []

    scheduler.stop()
    await asyncio.wait_for(task, timeout=1)

    assert service.synced == []
