from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.api.errors import DomainError
from app.core.secrets import MemorySecretStore
from app.remote.credentials import CredentialChannelSpec, ProviderCredentialSpec
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.registry import ProviderRegistry
from app.storage.models import SettingRecord


class _StubProvider:
    identity = ProviderIdentity(
        name="acme", label="Acme Docs", capabilities=ProviderCapabilities()
    )

    async def close(self) -> None:
        return None


def _install_provider(client: TestClient, *, tester=None, normalizer=None) -> None:
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
                normalizer=normalizer,
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


def _reject_foreign_host(value: str) -> str:
    """Stand-in for a channel whose secret is a constrained URL.

    Mirrors the real webhook guard: only an allow-listed host survives, so the
    generic save path cannot be turned into a request-forgery primitive. It also
    trims, like the real normalizer, so the canonicalization test below asserts
    the normalizer's output rather than the endpoint's ``strip()``.
    """
    candidate = value.strip()
    if not candidate.startswith("https://hooks.example.com/"):
        raise DomainError("ACME_ENDPOINT_INVALID", "地址无效", 422, False)
    return candidate


def test_channel_normalizer_rejects_value_the_save_path_would_otherwise_accept(
    client: TestClient, auth_headers
) -> None:
    """A constrained secret must be validated by the channel, not the API.

    The save endpoint used to ``strip()`` whatever it was given, so a channel
    whose secret is really a URL silently accepted any host and stored it.
    """
    _install_provider(client, normalizer=_reject_foreign_host)
    store = client.app.state.credential_store

    response = client.put(
        "/api/remote/providers/acme/credentials/api",
        headers=auth_headers,
        json={"secret": "https://attacker.example.com/steal"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ACME_ENDPOINT_INVALID"
    # The rejected value must never reach the keychain, or the probe would
    # still be able to use it even though the save was refused.
    assert store.secret_store.get("acme:token") is None


def test_channel_normalizer_canonicalizes_the_stored_value(
    client: TestClient, auth_headers
) -> None:
    _install_provider(client, normalizer=_reject_foreign_host)
    store = client.app.state.credential_store

    response = client.put(
        "/api/remote/providers/acme/credentials/api",
        headers=auth_headers,
        json={"secret": "  https://hooks.example.com/ok  "},
    )

    assert response.status_code == 200, response.text
    # Trimmed by the normalizer, not by the endpoint, so a provider that needs
    # a stricter canonical form owns it.
    assert store.secret_store.get("acme:token") == "https://hooks.example.com/ok"


def test_channel_without_normalizer_keeps_storing_any_value(
    client: TestClient, auth_headers
) -> None:
    """Opaque secrets (tokens) must not be run through URL validation."""
    _install_provider(client)

    response = client.put(
        "/api/remote/providers/acme/credentials/api",
        headers=auth_headers,
        json={"secret": "  arbitrary-token  "},
    )

    assert response.status_code == 200, response.text
    assert client.app.state.credential_store.secret_store.get("acme:token") == (
        "arbitrary-token"
    )


def test_list_credentials_exposes_channel_declared_presentation_hints(
    client: TestClient, auth_headers
) -> None:
    """URL-shaped channels carry their own placeholder and help link.

    The generic form used to hardcode "… Token", which mislabels a webhook.
    """

    def _install_with_hints(target: TestClient) -> None:
        spec = ProviderCredentialSpec(
            provider="acme",
            channels=(
                CredentialChannelSpec(
                    name="api",
                    label="Acme 机器人",
                    has_secret=True,
                    default_secret_ref="acme:hook",
                    secret_placeholder="https://hooks.example.com/…",
                    help_url="https://example.com/add-bot",
                    help_label="添加机器人",
                ),
            ),
        )
        registry = ProviderRegistry(target.app.state.credential_store)
        registry.register(_StubProvider(), always_configured=True, credential_spec=spec)
        target.app.state.remote_registry = registry

    _install_with_hints(client)

    channel = client.get(
        "/api/remote/providers/acme/credentials", headers=auth_headers
    ).json()[0]

    assert channel["secretPlaceholder"] == "https://hooks.example.com/…"
    assert channel["helpUrl"] == "https://example.com/add-bot"
    assert channel["helpLabel"] == "添加机器人"


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


class TestWebhookChannelCannotBeUsedForRequestForgery:
    """End-to-end guard on the *production* Feishu wiring.

    Regression: the generic credential endpoint stripped the submitted value
    and stored it without consulting the channel, so any host could be saved
    and then POSTed to by the channel's own tester.
    """

    @pytest.mark.parametrize(
        "hostile",
        [
            "https://attacker.example.com/steal",
            "http://127.0.0.1:9/internal-admin",
            "https://open.feishu.cn.evil.example/open-apis/bot/v2/hook/x",
            "https://open.feishu.cn/admin",
        ],
    )
    def test_generic_save_refuses_a_foreign_webhook_host(
        self, production_client: TestClient, auth_headers, hostile: str
    ) -> None:
        secret_store = production_client.app.state.secret_store

        response = production_client.put(
            "/api/remote/providers/feishu/credentials/webhook",
            headers=auth_headers,
            json={"secret": hostile},
        )

        assert response.status_code == 422, response.text
        assert secret_store.get("feishu:webhook") is None

    def test_generic_save_accepts_and_canonicalizes_a_real_webhook(
        self, production_client: TestClient, auth_headers
    ) -> None:
        secret_store = production_client.app.state.secret_store
        valid = "https://open.feishu.cn/open-apis/bot/v2/hook/private-token"

        response = production_client.put(
            "/api/remote/providers/feishu/credentials/webhook",
            headers=auth_headers,
            json={"secret": f"  {valid}  "},
        )

        assert response.status_code == 200, response.text
        assert secret_store.get("feishu:webhook") == valid
        assert valid not in response.text

    def test_token_channels_are_unaffected_by_the_url_guard(
        self, production_client: TestClient, auth_headers
    ) -> None:
        """Opaque secrets must not be run through webhook URL validation."""
        response = production_client.put(
            "/api/remote/providers/yuque/credentials/api",
            headers=auth_headers,
            json={"secret": "not-a-url-at-all"},
        )

        assert response.status_code == 200, response.text
        assert production_client.app.state.secret_store.get("yuque-api:token") == (
            "not-a-url-at-all"
        )


@respx.mock
def test_yuque_api_token_stays_out_of_the_settings_table_and_activates_the_api_gateway(
    production_client: TestClient, auth_headers
) -> None:
    """The generic save path must wire the real Yuque provider correctly.

    The stub-provider tests above prove the generic endpoint's mechanics, but
    only the production assembly knows two things they cannot: that the token
    reaches the keychain under ``yuque-api:token`` and never touches the
    ``settings`` table, and that verifying the channel flips YuqueProvider's
    gateway preference from the browser session to the API. Losing either would
    be silent — the request still returns 200 and the row still says
    ``verified`` — so they are asserted against the real registry here.
    """
    user_route = respx.get("https://www.yuque.com/api/v2/user").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"login": "tan", "name": "谭凌峰"}},
        )
    )
    secret_store: MemorySecretStore = production_client.app.state.secret_store

    response = production_client.put(
        "/api/remote/providers/yuque/credentials/api",
        headers=auth_headers,
        json={"secret": "yuque-private-token"},
    )

    assert response.status_code == 200, response.text
    # The response is a channel view and must never echo the secret back.
    assert "yuque-private-token" not in response.text
    assert secret_store.get("yuque-api:token") == "yuque-private-token"
    # Saving alone must not mark the channel usable, so Yuque keeps the browser
    # gateway until the token actually verifies.
    assert response.json()["state"] == "unverified"
    provider = production_client.app.state.remote_registry.get("yuque")
    assert provider._active() is provider.web_gateway

    response = production_client.post(
        "/api/remote/providers/yuque/credentials/api/test", headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert response.json()["label"] == "谭***峰"
    assert user_route.called
    # The real production probe must now prefer the API over the browser session.
    assert provider._active() is provider.api_gateway
    assert provider._api_available() is True

    credential = production_client.app.state.credential_store.get("yuque", "api")
    assert credential is not None and credential.state == "verified"
    # Privacy: the token belongs in the keychain only, never in SQLite.
    with production_client.app.state.database.session() as session:
        values = [record.value for record in session.query(SettingRecord).all()]
    assert "yuque-private-token" not in json.dumps(values)


@respx.mock
def test_a_rejected_yuque_api_token_leaves_the_browser_gateway_in_charge(
    production_client: TestClient, auth_headers
) -> None:
    """A failed verification must not promote the API gateway.

    The mirror image of the test above: a token Yuque rejects has to leave the
    channel ``unverified`` so ``_api_available()`` stays false, otherwise the
    provider would start sending every read/write with a credential the vendor
    already refused.
    """
    respx.get("https://www.yuque.com/api/v2/user").mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )
    production_client.put(
        "/api/remote/providers/yuque/credentials/api",
        headers=auth_headers,
        json={"secret": "bad-token"},
    )

    response = production_client.post(
        "/api/remote/providers/yuque/credentials/api/test", headers=auth_headers
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "YUQUE_API_AUTH_FAILED"
    provider = production_client.app.state.remote_registry.get("yuque")
    assert provider._active() is provider.web_gateway
    assert provider._api_available() is False
    credential = production_client.app.state.credential_store.get("yuque", "api")
    assert credential is not None and credential.state == "unverified"


@respx.mock
def test_production_feishu_webhook_test_sends_the_verification_message(
    production_client: TestClient, auth_headers
) -> None:
    """The production Feishu spec's tester must actually post the message.

    ``FEISHU_CREDENTIAL_SPEC`` declares ``tester=test_feishu_webhook``, and the
    generic test endpoint calls whatever the spec names. Nothing else asserts
    that the real spec's tester reaches the network and sends the bot's
    verification text, so a wiring slip would pass every other test here.
    """
    route = respx.post(
        "https://open.feishu.cn/open-apis/bot/v2/hook/private-token"
    ).mock(return_value=httpx.Response(200, json={"StatusCode": 0, "StatusMessage": "success"}))
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/private-token"
    save = production_client.put(
        "/api/remote/providers/feishu/credentials/webhook",
        headers=auth_headers,
        json={"secret": webhook},
    )

    assert save.status_code == 200, save.text
    assert save.json()["state"] == "unverified"

    response = production_client.post(
        "/api/remote/providers/feishu/credentials/webhook/test", headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert route.called
    assert json.loads(route.calls.last.request.content) == {
        "msg_type": "text",
        "content": {"text": "DocMind 飞书绑定验证成功"},
    }
    credential = production_client.app.state.credential_store.get("feishu", "webhook")
    assert credential is not None and credential.state == "verified"
