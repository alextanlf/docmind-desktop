def test_empty_scope_is_rejected_by_memory_list(client, auth_headers):
    response = client.get("/api/memories?repositoryIds=", headers=auth_headers)
    assert response.status_code == 422
