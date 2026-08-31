from __future__ import annotations

from app.core.secrets import KeyringSecretStore, MemorySecretStore


def test_memory_secret_store_keeps_values_outside_persistent_settings() -> None:
    store = MemorySecretStore()

    store.set("model-api-key", "secret-value")

    assert store.get("model-api-key") == "secret-value"
    store.delete("model-api-key")
    assert store.get("model-api-key") is None


def test_keyring_secret_store_uses_docmind_service_and_requested_account(monkeypatch) -> None:
    calls: list[tuple[str, str, str | None]] = []

    class FakeKeyring:
        @staticmethod
        def get_password(service: str, account: str) -> str | None:
            calls.append((service, account, None))
            return "stored-secret"

        @staticmethod
        def set_password(service: str, account: str, value: str) -> None:
            calls.append((service, account, value))

        @staticmethod
        def delete_password(service: str, account: str) -> None:
            calls.append((service, account, None))

    monkeypatch.setattr("app.core.secrets.keyring", FakeKeyring)
    store = KeyringSecretStore()

    assert store.get("model-api-key") == "stored-secret"
    store.set("model-api-key", "new-secret")
    store.delete("model-api-key")

    assert calls == [
        ("com.docmind.desktop", "model-api-key", None),
        ("com.docmind.desktop", "model-api-key", "new-secret"),
        ("com.docmind.desktop", "model-api-key", None),
    ]
