from __future__ import annotations


def test_versions_endpoint_returns_bounded_history(client, auth_headers) -> None:
    client.app.state.version_store.snapshot("doc-1", "State", "# One")

    response = client.get("/api/documents/doc-1/versions", headers=auth_headers)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["versionNo"] == 1
