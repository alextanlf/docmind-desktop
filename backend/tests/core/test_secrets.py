from __future__ import annotations

import logging

import pytest

from app.api.errors import DomainError
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


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_keyring_backend_failures_are_redacted_domain_errors(
    monkeypatch, caplog: pytest.LogCaptureFixture, operation: str
) -> None:
    backend_detail = "keychain backend rejected secret-value"

    class FakeKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        @staticmethod
        def get_password(service: str, account: str) -> None:
            del service, account
            raise RuntimeError(backend_detail)

        @staticmethod
        def set_password(service: str, account: str, value: str) -> None:
            del service, account, value
            raise RuntimeError(backend_detail)

        @staticmethod
        def delete_password(service: str, account: str) -> None:
            del service, account
            raise RuntimeError(backend_detail)

    monkeypatch.setattr("app.core.secrets.keyring", FakeKeyring)
    store = KeyringSecretStore()

    with caplog.at_level(logging.DEBUG), pytest.raises(DomainError) as error:
        if operation == "get":
            store.get("model-api-key")
        elif operation == "set":
            store.set("model-api-key", "secret-value")
        else:
            store.delete("model-api-key")

    assert error.value.code == "SECRET_STORE_FAILED"
    assert error.value.message == "无法访问系统钥匙串，请稍后重试"
    assert "secret-value" not in str(error.value)
    assert backend_detail not in str(error.value)
    assert "secret-value" not in caplog.text
    assert backend_detail not in caplog.text
