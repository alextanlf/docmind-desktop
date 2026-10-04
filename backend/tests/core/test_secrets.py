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


def test_windows_credentials_are_pinned_to_this_machine(monkeypatch) -> None:
    """Windows 后端默认 CRED_PERSIST_ENTERPRISE 会让 API Key 随域配置漫游。"""

    class FakeWindowsBackend:
        def __init__(self) -> None:
            self.persist = "enterprise"

    backend = FakeWindowsBackend()

    class FakeKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        @staticmethod
        def get_keyring() -> FakeWindowsBackend:
            return backend

        @staticmethod
        def get_password(service: str, account: str) -> str:
            return "secret"

        @staticmethod
        def set_password(service: str, account: str, value: str) -> None:
            pass

        @staticmethod
        def delete_password(service: str, account: str) -> None:
            pass

    monkeypatch.setattr("app.core.secrets.sys.platform", "win32")
    monkeypatch.setattr("app.core.secrets.keyring", FakeKeyring)

    KeyringSecretStore()

    assert backend.persist == "local machine"


def test_non_windows_platforms_are_left_untouched(monkeypatch) -> None:
    """macOS 钥匙串 / Linux SecretService 没有漫游维度，不该被设置 persist。"""

    class FakeKeychainBackend:
        pass

    class FakeKeyring:
        @staticmethod
        def get_keyring() -> FakeKeychainBackend:
            raise AssertionError("非 Windows 平台不应查询 keyring 后端")

    monkeypatch.setattr("app.core.secrets.sys.platform", "darwin")
    monkeypatch.setattr("app.core.secrets.keyring", FakeKeyring)

    # 不抛异常即通过 —— 若上面的 get_keyring 被调用，测试会失败。
    KeyringSecretStore()


def test_persistence_failure_does_not_block_secret_storage(monkeypatch) -> None:
    """设不进 persist 只应保持库默认行为，不该让整个应用起不来。"""

    class HostileBackend:
        @property
        def persist(self) -> str:
            return "enterprise"

        @persist.setter
        def persist(self, value: str) -> None:
            raise RuntimeError("win32cred unavailable")

    class FakeKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        @staticmethod
        def get_keyring() -> HostileBackend:
            return HostileBackend()

        @staticmethod
        def get_password(service: str, account: str) -> str:
            return "still-readable"

        @staticmethod
        def set_password(service: str, account: str, value: str) -> None:
            pass

        @staticmethod
        def delete_password(service: str, account: str) -> None:
            pass

    monkeypatch.setattr("app.core.secrets.sys.platform", "win32")
    monkeypatch.setattr("app.core.secrets.keyring", FakeKeyring)

    store = KeyringSecretStore()

    assert store.get("model-api-key") == "still-readable"

