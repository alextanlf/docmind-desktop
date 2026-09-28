from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.core.secrets import MemorySecretStore
from app.remote.credentials import (
    CredentialChannelSpec,
    CredentialStore,
    ProviderCredentialSpec,
)
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.registry import ProviderRegistry
from app.storage.database import Database
from app.storage.models import ProviderCredentialState


@pytest.fixture
def secret_store() -> MemorySecretStore:
    return MemorySecretStore()


@pytest.fixture
def store(database: Database, secret_store: MemorySecretStore) -> CredentialStore:
    return CredentialStore(database, secret_store)


class StubProvider:
    def __init__(self, name: str) -> None:
        self.identity = ProviderIdentity(
            name=name, label="标签", capabilities=ProviderCapabilities()
        )

    async def close(self) -> None:
        return None


def _spec(provider: str = "acme") -> ProviderCredentialSpec:
    return ProviderCredentialSpec(
        provider=provider,
        channels=(
            CredentialChannelSpec(name="web", label="网页", has_secret=False),
            CredentialChannelSpec(
                name="api", label="API", has_secret=True, default_secret_ref="acme:token"
            ),
        ),
    )


def test_save_secret_marks_unverified_and_keeps_secret_in_keychain(
    store: CredentialStore, secret_store: MemorySecretStore
) -> None:
    store.save_secret("acme", "api", "secret-token", "acme:token")

    record = store.get("acme", "api")
    assert record is not None
    assert record.state == ProviderCredentialState.UNVERIFIED.value
    assert record.secret_ref == "acme:token"
    assert secret_store.get("acme:token") == "secret-token"

    state = store.channel_state("acme", "api", _spec().channel("api"))
    assert state.configured is True
    assert state.verified is False


def test_save_empty_secret_clears_keychain_and_configured_flag(
    store: CredentialStore, secret_store: MemorySecretStore
) -> None:
    store.save_secret("acme", "api", "secret-token", "acme:token")

    store.save_secret("acme", "api", "", "acme:token")

    assert secret_store.get("acme:token") is None
    state = store.channel_state("acme", "api", _spec().channel("api"))
    assert state.configured is False
    assert state.verified is False


def test_mark_state_tracks_verified_with_account_label(store: CredentialStore) -> None:
    store.save_secret("acme", "api", "secret-token", "acme:token")

    store.mark_state("acme", "api", "verified", account_label="acme-user")

    state = store.channel_state("acme", "api", _spec().channel("api"))
    assert state.verified is True
    assert state.account_label == "acme-user"
    assert store.any_verified("acme", ("web", "api")) is True


def test_any_verified_only_counts_declared_channels(store: CredentialStore) -> None:
    """A Feishu *webhook* binding must not mark the Feishu KB provider configured."""
    store.mark_state("feishu", "webhook", "verified")

    assert store.any_verified("feishu", ("app",)) is False
    assert store.any_verified("feishu", ("webhook",)) is True


def test_clear_removes_secret_and_marks_disconnected(
    store: CredentialStore, secret_store: MemorySecretStore
) -> None:
    store.save_secret("acme", "api", "secret-token", "acme:token")
    store.mark_state("acme", "api", "verified", account_label="acme-user")

    store.clear("acme", "api")

    assert secret_store.get("acme:token") is None
    state = store.channel_state("acme", "api", _spec().channel("api"))
    assert state.state == ProviderCredentialState.DISCONNECTED.value
    assert state.configured is False
    assert state.account_label is None


def test_save_secret_rolls_back_keychain_when_state_row_fails(
    store: CredentialStore, secret_store: MemorySecretStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_store.set("acme:token", "previous")

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "_upsert", _boom)

    with pytest.raises(RuntimeError):
        store.save_secret("acme", "api", "new-secret", "acme:token")

    assert secret_store.get("acme:token") == "previous"


def test_registry_default_probe_tracks_credential_state(
    database: Database, secret_store: MemorySecretStore
) -> None:
    store = CredentialStore(database, secret_store)
    registry = ProviderRegistry(store)
    registry.register(StubProvider("acme"), credential_spec=_spec())

    assert registry.is_configured("acme") is False

    store.save_secret("acme", "api", "secret-token", "acme:token")
    assert registry.is_configured("acme") is False

    store.mark_state("acme", "api", "verified")
    assert registry.is_configured("acme") is True


def test_registry_probe_ignores_undeclared_channels(
    database: Database, secret_store: MemorySecretStore
) -> None:
    store = CredentialStore(database, secret_store)
    registry = ProviderRegistry(store)
    registry.register(StubProvider("feishu"), credential_spec=_spec("feishu"))

    store.mark_state("feishu", "webhook", "verified")

    assert registry.is_configured("feishu") is False


def test_registry_without_spec_or_probe_is_not_configured(
    database: Database, secret_store: MemorySecretStore
) -> None:
    registry = ProviderRegistry(CredentialStore(database, secret_store))
    registry.register(StubProvider("acme"))

    assert registry.is_configured("acme") is False


def test_credential_spec_lookup(registry_name: str = "acme") -> None:
    registry = ProviderRegistry()
    registry.register(StubProvider(registry_name), always_configured=True, credential_spec=_spec())

    spec = registry.credential_spec(registry_name)
    assert spec is not None
    assert [channel.name for channel in spec.channels] == ["web", "api"]
    assert registry.credential_spec("missing") is None
