"""Provider-specific behaviour of the yuque remote provider API surface."""
from __future__ import annotations

import asyncio

from app.remote.fake import FakeRemoteProvider


def test_remote_routes_require_runtime_token(client) -> None:
    response = client.get("/api/remote/providers")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_status_returns_masked_logged_in_state(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    asyncio.run(remote_provider.begin_login())

    response = client.get("/api/remote/providers/yuque/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "loggedIn": True,
        "accountLabel": "f***e",
        "requiresLogin": False,
    }
    assert client.app.state.settings_service.setting_store.get("yuque-web.connected") is None


def test_status_records_a_disconnected_web_session(client, auth_headers) -> None:
    response = client.get("/api/remote/providers/yuque/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["requiresLogin"] is True
    assert client.app.state.settings_service.setting_store.get("yuque-web.connected") is None


def test_login_route_logs_in_and_records_the_session(client, auth_headers) -> None:
    response = client.post("/api/remote/providers/yuque/login", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["loggedIn"] is True
    assert response.json()["requiresLogin"] is False
    assert client.app.state.settings_service.setting_store.get("yuque-web.connected") is None


def test_install_browser_route_uses_the_provider(
    client, auth_headers, remote_provider: FakeRemoteProvider
) -> None:
    response = client.post(
        "/api/remote/providers/yuque/browser/install", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["installed"] is True
