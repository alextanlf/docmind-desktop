from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.errors import DomainError
from app.remote.credentials import CredentialChannelSpec, ProviderCredentialSpec
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.registry import ProviderRegistry


class _StubProvider:
    identity = ProviderIdentity(
        name="acme", label="Acme Docs", capabilities=ProviderCapabilities()
    )

    async def close(self) -> None:
        return None


def _install_provider(client: TestClient, *, tester=None) -> None:
    spec = ProviderCredentialSpec(
        provider="acme",
        channels=(
            CredentialChannelSpec(name="web", label="Acme 网页", has_secret=False),
            CredentialChannelSpec(
                name="api",
                label="Acme API",
                has_secret=True,
                default_secret_ref="acme:token",
                tester=tester,
            ),
        ),
    )
    registry = ProviderRegistry(client.app.state.credential_store)
    registry.register(_StubProvider(), always_configured=True, credential_spec=spec)
    client.app.state.remote_registry = registry


async def _passing_tester(secret: str) -> str | None:
    return f"user-{secret[:4]}"


async def _failing_tester(secret: str) -> str | None:
    raise DomainError("ACME_AUTH_FAILED", "凭据无效", 401, False)


def test_credentials_require_runtime_token(client: TestClient) -> None:
    _install_provider(client)
    responses = [
        client.get("/api/remote/providers/acme/credentials"),
        client.put("/api/remote/providers/acme/credentials/api", json={"secret": "x"}),
        client.post("/api/remote/providers/acme/credentials/api/test"),
        client.delete("/api/remote/providers/acme/credentials/api"),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401]


def test_list_credentials_reports_declared_channels(client: TestClient, auth_headers) -> None:
    _install_provider(client)

    response = client.get("/api/remote/providers/acme/credentials", headers=auth_headers)

    assert response.status_code == 200, response.text
    channels = {channel["channel"]: channel for channel in response.json()}
    assert set(channels) == {"web", "api"}
    assert channels["api"]["hasSecret"] is True
    assert channels["api"]["state"] == "disconnected"
    assert channels["api"]["configured"] is False
    assert channels["web"]["hasSecret"] is False


def test_list_credentials_for_unknown_provider_is_404(client: TestClient, auth_headers) -> None:
    response = client.get("/api/remote/providers/missing/credentials", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REMOTE_PROVIDER_UNKNOWN"


def test_save_and_delete_secret_round_trip(client: TestClient, auth_headers) -> None:
    _install_provider(client)
    store = client.app.state.credential_store

    response = client.put(
        "/api/remote/providers/acme/credentials/api",
        headers=auth_headers,
        json={"secret": "token-123"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "unverified"
    assert body["configured"] is True
    assert "token-123" not in response.text
    assert store.secret_store.get("acme:token") == "token-123"

    response = client.delete(
        "/api/remote/providers/acme/credentials/api", headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "disconnected"
    assert response.json()["configured"] is False
    assert store.secret_store.get("acme:token") is None


def test_save_on_secretless_channel_is_rejected(client: TestClient, auth_headers) -> None:
    _install_provider(client)

    response = client.put(
        "/api/remote/providers/acme/credentials/web",
        headers=auth_headers,
        json={"secret": "x"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REMOTE_CAPABILITY_UNSUPPORTED"


def test_unknown_channel_is_rejected(client: TestClient, auth_headers) -> None:
    _install_provider(client)

    response = client.put(
        "/api/remote/providers/acme/credentials/carrier-pigeon",
        headers=auth_headers,
        json={"secret": "x"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REMOTE_CAPABILITY_UNSUPPORTED"


def test_test_endpoint_requires_a_saved_secret(client: TestClient, auth_headers) -> None:
    _install_provider(client, tester=_passing_tester)

    response = client.post(
        "/api/remote/providers/acme/credentials/api/test", headers=auth_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REMOTE_CREDENTIAL_REQUIRED"


def test_test_endpoint_marks_verified_with_account_label(client: TestClient, auth_headers) -> None:
    _install_provider(client, tester=_passing_tester)
    client.put(
        "/api/remote/providers/acme/credentials/api",
        headers=auth_headers,
        json={"secret": "token-123"},
    )

    response = client.post(
        "/api/remote/providers/acme/credentials/api/test", headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert response.json()["label"] == "user-toke"

    state = client.app.state.credential_store.channel_state(
        "acme", "api",
        client.app.state.remote_registry.credential_spec("acme").channel("api"),
    )
    assert state.verified is True
    assert state.account_label == "user-toke"


def test_failed_test_resets_channel_to_unverified(client: TestClient, auth_headers) -> None:
    _install_provider(client, tester=_failing_tester)
    client.put(
        "/api/remote/providers/acme/credentials/api",
        headers=auth_headers,
        json={"secret": "bad-token"},
    )

    response = client.post(
        "/api/remote/providers/acme/credentials/api/test", headers=auth_headers
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ACME_AUTH_FAILED"
    record = client.app.state.credential_store.get("acme", "api")
    assert record is not None
    assert record.state == "unverified"


def test_channel_without_tester_cannot_be_tested(client: TestClient, auth_headers) -> None:
    _install_provider(client, tester=None)

    response = client.post(
        "/api/remote/providers/acme/credentials/api/test", headers=auth_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REMOTE_CAPABILITY_UNSUPPORTED"


@pytest.mark.parametrize("provider", ["../admin", "a b"])
def test_provider_path_segment_is_validated(client: TestClient, auth_headers, provider) -> None:
    response = client.get(f"/api/remote/providers/{provider}/credentials", headers=auth_headers)

    assert response.status_code in {400, 404, 422}


class _OAuthStubProvider:
    """Provider whose interactive login maps onto a non-'web' channel."""

    identity = ProviderIdentity(
        name="globex", label="Globex Docs", capabilities=ProviderCapabilities()
    )

    async def login_status(self):
        from app.schemas.remote import LoginStatus

        return LoginStatus(logged_in=True, account_label="g***x", requires_login=False)

    async def begin_login(self):
        from app.schemas.remote import LoginResult

        return LoginResult(logged_in=True, account_label="g***x", requires_login=False)

    async def close(self) -> None:
        return None


def test_login_marks_the_spec_declared_login_channel(client: TestClient, auth_headers) -> None:
    spec = ProviderCredentialSpec(
        provider="globex",
        channels=(
            CredentialChannelSpec(name="app", label="Globex 应用", has_secret=True),
            CredentialChannelSpec(name="user", label="Globex 账号授权", has_secret=False),
        ),
        login_channel="user",
    )
    registry = ProviderRegistry(client.app.state.credential_store)
    registry.register(_OAuthStubProvider(), always_configured=True, credential_spec=spec)
    client.app.state.remote_registry = registry

    response = client.post("/api/remote/providers/globex/login", headers=auth_headers)

    assert response.status_code == 200, response.text
    record = client.app.state.credential_store.get("globex", "user")
    assert record is not None
    assert record.state == "verified"
