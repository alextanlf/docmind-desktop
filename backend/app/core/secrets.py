from __future__ import annotations

from typing import Protocol

import keyring

KEYRING_SERVICE = "com.docmind.desktop"


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class KeyringSecretStore:
    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, name)
        except Exception:  # noqa: BLE001 - Keyring backends expose provider-specific exceptions.
            raise _secret_store_error() from None

    def set(self, name: str, value: str) -> None:
        try:
            keyring.set_password(KEYRING_SERVICE, name, value)
        except Exception:  # noqa: BLE001 - Keyring backends expose provider-specific exceptions.
            raise _secret_store_error() from None

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:  # noqa: BLE001 - Keyring backends expose provider-specific exceptions.
            raise _secret_store_error() from None


def _secret_store_error():
    from app.api.errors import DomainError

    return DomainError("SECRET_STORE_FAILED", "无法访问系统钥匙串，请稍后重试", 503, True, "稍后重试")


class MemorySecretStore:
    """In-process secret store for tests and explicitly injected fake services."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)
