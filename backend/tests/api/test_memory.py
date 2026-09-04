def test_memory_list_requires_repository_scope(client, auth_headers):
    response = client.get("/api/memories", headers=auth_headers)
    assert response.status_code == 422

