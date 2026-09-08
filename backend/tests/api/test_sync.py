from __future__ import annotations

from app.schemas.sync import SyncOutcome


class FakeSyncService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def sync_repository(self, repository_id: str) -> SyncOutcome:
        self.calls.append(repository_id)
        return SyncOutcome(
            repository_id=repository_id,
            added=1,
            changed=2,
            deleted=3,
            unchanged=4,
            failed=0,
            started_at="s",
            finished_at="f",
        )


def test_sync_routes_require_token(client) -> None:
    assert client.post("/api/repositories/r1/sync").status_code == 401


def test_sync_post_delegates_and_returns_outcome(client, auth_headers) -> None:
    fake = FakeSyncService()
    client.app.state.sync_service = fake

    response = client.post("/api/repositories/r1/sync", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["added"] == 1
    assert fake.calls == ["r1"]


def test_sync_get_returns_last_synced_at(client, auth_headers) -> None:
    client.app.state.sync_state_store.set_last_synced_at(
        "r1", "2026-09-08T00:00:00+00:00"
    )

    response = client.get("/api/repositories/r1/sync", headers=auth_headers)

    assert response.json() == {"last_synced_at": "2026-09-08T00:00:00+00:00"}
