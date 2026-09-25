def test_search_settings_never_returns_key(client, auth_headers):
    response = client.put("/api/settings/web-search", headers=auth_headers, json={"mode": "auto", "maxResults": 5, "apiKey": "secret"})
    assert response.status_code == 200
    assert response.json()["webSearch"]["hasApiKey"] is True
    assert "secret" not in response.text


def test_search_settings_reject_invalid_mode(client, auth_headers):
    response = client.put("/api/settings/web-search", headers=auth_headers, json={"mode": "always", "maxResults": 5})
    assert response.status_code == 422


def test_search_settings_report_source_priority(client, auth_headers):
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "qwen",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "timeoutSeconds": 30,
            "apiKey": "model-secret",
        },
    )
    assert response.status_code == 200
    view = response.json()["webSearch"]
    assert view["modelSearchAvailable"] is True
    assert "qwen-plus" in view["modelSearchLabel"]
    assert view["freeFallbackAvailable"] is True
    assert view["hasApiKey"] is False


def test_search_settings_mark_unsupported_model_endpoints(client, auth_headers):
    response = client.get("/api/settings", headers=auth_headers)
    assert response.status_code == 200
    view = response.json()["webSearch"]
    assert view["modelSearchAvailable"] is False
    assert "不支持" in view["modelSearchLabel"]
    assert view["freeFallbackAvailable"] is True


def test_search_settings_skip_model_search_in_local_only_mode(client, auth_headers):
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "qwen",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "timeoutSeconds": 30,
            "apiKey": "model-secret",
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/settings/runtime",
        headers=auth_headers,
        json={"routing": {"mode": "local_only"}},
    )
    assert response.status_code == 200, response.text
    view = response.json()["webSearch"]
    assert view["modelSearchAvailable"] is False
    assert "本地模式" in view["modelSearchLabel"]


def test_model_native_provider_disabled_in_local_only_mode(client, auth_headers):
    response = client.put(
        "/api/settings/model",
        headers=auth_headers,
        json={
            "preset": "qwen",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "timeoutSeconds": 30,
            "apiKey": "model-secret",
        },
    )
    assert response.status_code == 200
    providers = client.app.state.search_service.provider.providers
    assert providers[0].name == "model"
    assert providers[0].available() is True

    response = client.post(
        "/api/settings/runtime",
        headers=auth_headers,
        json={"routing": {"mode": "local_only"}},
    )
    assert response.status_code == 200, response.text
    assert providers[0].available() is False



def test_search_settings_round_trip_query_rewrite(client, auth_headers):
    response = client.put(
        "/api/settings/web-search",
        headers=auth_headers,
        json={"mode": "auto", "maxResults": 5, "queryRewrite": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["webSearch"]["queryRewrite"] is False

    response = client.get("/api/settings", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["webSearch"]["queryRewrite"] is False


def test_search_settings_store_searxng_instance(client, auth_headers):
    response = client.put(
        "/api/settings/web-search",
        headers=auth_headers,
        json={
            "mode": "auto",
            "maxResults": 5,
            "queryRewrite": True,
            "searxngUrl": "https://searx.example.com/",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["webSearch"]["searxngUrl"] == "https://searx.example.com"

    providers = {provider.name: provider for provider in client.app.state.search_service.provider.providers}
    assert providers["searxng"].available() is True

    response = client.put(
        "/api/settings/web-search",
        headers=auth_headers,
        json={"mode": "auto", "maxResults": 5, "searxngUrl": ""},
    )
    assert response.status_code == 200
    assert response.json()["webSearch"]["searxngUrl"] == ""
    assert providers["searxng"].available() is False


def test_search_settings_reject_invalid_searxng_instance(client, auth_headers):
    response = client.put(
        "/api/settings/web-search",
        headers=auth_headers,
        json={"mode": "auto", "maxResults": 5, "searxngUrl": "ftp://searx.example.com"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SEARCH_SETTINGS_INVALID"


def test_searxng_slots_between_tavily_and_free_sources(client, auth_headers):
    providers = client.app.state.search_service.provider.providers
    assert [provider.name for provider in providers] == [
        "model",
        "tavily",
        "searxng",
        "bing",
        "duckduckgo",
    ]
