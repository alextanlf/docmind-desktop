def test_batch_endpoint_is_registered(client, auth_headers) -> None:
    response = client.get("/api/import-batches", headers=auth_headers)
    assert response.status_code == 200


def test_unsupported_batch_kind_returns_stable_validation_error(client, auth_headers) -> None:
    response = client.post(
        "/api/import-batches",
        headers=auth_headers,
        json={
            "kind": "web",
            "sourceId": "https://example.test",
            "repositoryId": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
