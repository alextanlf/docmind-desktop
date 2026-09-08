from __future__ import annotations

import asyncio

from app.yuque.gateway import FakeYuqueGateway


def test_yuque_status_route_requires_runtime_token(client) -> None:
    response = client.get("/api/yuque/status")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_yuque_login_route_uses_injected_gateway(client, auth_headers) -> None:
    fake_yuque = FakeYuqueGateway()
    client.app.state.yuque_gateway = fake_yuque

    response = client.post("/api/yuque/login", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["loggedIn"] is True
    assert response.json()["requiresLogin"] is False


def test_yuque_status_returns_masked_logged_in_state(client, auth_headers) -> None:
    fake_yuque = FakeYuqueGateway()
    asyncio.run(fake_yuque.begin_login())
    client.app.state.yuque_gateway = fake_yuque

    response = client.get("/api/yuque/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "loggedIn": True,
        "accountLabel": "f***e",
        "requiresLogin": False,
    }


def test_yuque_install_browser_route_uses_injected_gateway(client, auth_headers) -> None:
    fake_yuque = FakeYuqueGateway()
    client.app.state.yuque_gateway = fake_yuque

    response = client.post("/api/yuque/browser/install", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["installed"] is True
