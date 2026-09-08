from __future__ import annotations


class FakeConflictService:
    def __init__(self) -> None:
        self.resolved: list[tuple[str, str]] = []

    async def detect(self, repository_id: str) -> list:
        return []

    async def resolve(self, document_id: str, resolution) -> None:
        self.resolved.append((document_id, str(resolution)))


def test_resolve_conflict_endpoint(client, auth_headers) -> None:
    fake = FakeConflictService()
    client.app.state.conflict_service = fake

    response = client.post(
        "/api/documents/doc-1/conflict",
        headers=auth_headers,
        json={"resolution": "keep_local"},
    )

    assert response.status_code == 204
    assert fake.resolved == [("doc-1", "keep_local")]


def test_list_conflicts_endpoint(client, auth_headers) -> None:
    fake = FakeConflictService()
    client.app.state.conflict_service = fake

    response = client.get("/api/repositories/r1/conflicts", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []
