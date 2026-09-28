from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import AppSettings
from app.core.secrets import MemorySecretStore
from app.storage.models import SettingRecord
from tests.conftest import RUNTIME_TOKEN


@pytest.fixture
def production_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    """A client whose remote registry is assembled by production wiring.

    The shared ``client`` fixture injects a fake provider; the yuque API/web
    routing assertions below need the real ``YuqueProvider`` composition.
    """
    monkeypatch.setenv("DOCMIND_SESSION_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setenv("DOCMIND_DATA_DIR", str(tmp_path / "production-data"))
    monkeypatch.setenv("DOCMIND_ENVIRONMENT", "test")
    settings = AppSettings(
        session_token=SecretStr(RUNTIME_TOKEN),
        data_dir=tmp_path / "production-data",
        environment="test",
    )
    from app.main import create_app

    with TestClient(
        create_app(settings, secret_store=MemorySecretStore())
    ) as test_client:
        yield test_client


def test_connection_mutations_require_runtime_token(client) -> None:
    responses = [
        client.put("/api/settings/connections/yuque-api", json={"token": "secret"}),
        client.post("/api/settings/connections/yuque-api/test"),
        client.put("/api/settings/connections/feishu", json={"webhookUrl": "https://example.test"}),
        client.post("/api/settings/connections/feishu/test"),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401]


@respx.mock
def test_yuque_api_token_is_private_verified_and_becomes_the_active_gateway(
    production_client: TestClient, auth_headers
) -> None:
    app_secret_store: MemorySecretStore = production_client.app.state.secret_store
    user_route = respx.get("https://www.yuque.com/api/v2/user").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"login": "tan", "name": "谭凌峰"}},
        )
    )

    response = production_client.put(
        "/api/settings/connections/yuque-api",
        headers=auth_headers,
        json={"token": "yuque-private-token"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["yuqueApi"] == {
        "configured": True,
        "verified": False,
        "label": None,
        "active": False,
    }
    assert "yuque-private-token" not in response.text
    assert app_secret_store.get("yuque-api:token") == "yuque-private-token"

    response = production_client.post(
        "/api/settings/connections/yuque-api/test", headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "connected": True,
        "message": "语雀 API 已连接，后续语雀读写将优先使用 API",
        "label": "谭***峰",
    }
    assert user_route.called
    assert production_client.get("/api/settings", headers=auth_headers).json()["yuqueApi"] == {
        "configured": True,
        "verified": True,
        "label": "谭***峰",
        "active": True,
    }
    provider = production_client.app.state.remote_registry.get("yuque")
    assert provider._active() is provider.api_gateway
    with production_client.app.state.database.session() as session:
        values = [record.value for record in session.query(SettingRecord).all()]
    assert "yuque-private-token" not in json.dumps(values)


@respx.mock
def test_invalid_yuque_api_token_stays_inactive(production_client: TestClient, auth_headers) -> None:
    respx.get("https://www.yuque.com/api/v2/user").mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )
    production_client.put(
        "/api/settings/connections/yuque-api",
        headers=auth_headers,
        json={"token": "bad-token"},
    )

    response = production_client.post(
        "/api/settings/connections/yuque-api/test", headers=auth_headers
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "YUQUE_API_AUTH_FAILED"
    assert (
        production_client.get("/api/settings", headers=auth_headers).json()["yuqueApi"]["active"]
        is False
    )
    provider = production_client.app.state.remote_registry.get("yuque")
    assert provider._active() is provider.web_gateway


@respx.mock
def test_feishu_webhook_is_private_and_binding_sends_a_verification_message(
    client, auth_headers, app_secret_store: MemorySecretStore
) -> None:
    route = respx.post(
        "https://open.feishu.cn/open-apis/bot/v2/hook/private-token"
    ).mock(return_value=httpx.Response(200, json={"StatusCode": 0, "StatusMessage": "success"}))
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/private-token"

    response = client.put(
        "/api/settings/connections/feishu",
        headers=auth_headers,
        json={"webhookUrl": webhook},
    )

    assert response.status_code == 200, response.text
    assert response.json()["feishu"] == {"configured": True, "verified": False, "label": None}
    assert webhook not in response.text
    assert app_secret_store.get("feishu:webhook") == webhook

    response = client.post("/api/settings/connections/feishu/test", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert route.called
    assert json.loads(route.calls.last.request.content) == {
        "msg_type": "text",
        "content": {"text": "DocMind 飞书绑定验证成功"},
    }
    assert client.get("/api/settings", headers=auth_headers).json()["feishu"] == {
        "configured": True,
        "verified": True,
        "label": None,
    }


def test_connection_bindings_can_be_removed(client, auth_headers) -> None:
    client.put(
        "/api/settings/connections/yuque-api",
        headers=auth_headers,
        json={"token": "token"},
    )
    client.put(
        "/api/settings/connections/feishu",
        headers=auth_headers,
        json={"webhookUrl": "https://open.feishu.cn/open-apis/bot/v2/hook/test"},
    )

    response = client.put(
        "/api/settings/connections/yuque-api",
        headers=auth_headers,
        json={"token": ""},
    )
    assert response.status_code == 200
    assert response.json()["yuqueApi"]["configured"] is False

    response = client.put(
        "/api/settings/connections/feishu",
        headers=auth_headers,
        json={"webhookUrl": ""},
    )
    assert response.status_code == 200
    assert response.json()["feishu"]["configured"] is False


def test_invalid_feishu_webhook_is_rejected(client, auth_headers) -> None:
    response = client.put(
        "/api/settings/connections/feishu",
        headers=auth_headers,
        json={"webhookUrl": "https://example.com/open-apis/bot/v2/hook/token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FEISHU_WEBHOOK_INVALID"
