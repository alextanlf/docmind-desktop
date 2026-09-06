def test_search_settings_never_returns_key(client, auth_headers):
    response = client.put("/api/settings/web-search", headers=auth_headers, json={"mode": "auto", "maxResults": 5, "apiKey": "secret"})
    assert response.status_code == 200
    assert response.json()["webSearch"]["hasApiKey"] is True
    assert "secret" not in response.text


def test_search_settings_reject_invalid_mode(client, auth_headers):
    response = client.put("/api/settings/web-search", headers=auth_headers, json={"mode": "always", "maxResults": 5})
    assert response.status_code == 422
