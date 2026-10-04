from __future__ import annotations

import sys
from typing import Protocol

import keyring

KEYRING_SERVICE = "com.docmind.desktop"


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


def _lock_secret_persistence_to_this_machine() -> None:
    """把 Windows 凭据的持久化范围从「企业漫游」改成「仅本机」。

    keyring 的 Windows 后端默认用 ``CRED_PERSIST_ENTERPRISE``，凭据会随用户的
    域漫游配置同步到其他机器。DocMind 存的是模型厂商 API Key 和远程知识库
    凭据，不该跟着账号走到别的电脑上，所以显式降级为 ``CRED_PERSIST_LOCAL_MACHINE``
    ——仍然由 DPAPI 用用户密钥加密，但只在本机凭据管理器里留存。

    平台差异：macOS 钥匙串与 Linux SecretService 没有「漫游」这个维度，
    凭据本来就只在本机，所以这个调用只在 Windows 上执行。

    失败不致命：设不进去只是保持库默认行为（仍能正常存取），不值得因此让整个
    应用起不来。真正读不到凭据时 ``_safe_secret_get`` 那侧会表现为「未配置」。
    """
    if sys.platform != "win32":
        return
    backend = keyring.get_keyring()
    # 只有 Windows 后端有 persist 属性；其他后端设了也没意义。
    if not hasattr(backend, "persist"):
        return
    try:
        # Persistence 描述符接受 'local machine' 字符串，内部转成 win32cred 常量。
        backend.persist = "local machine"  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 拿到底层 win32cred 失败时保持默认即可
        return


class KeyringSecretStore:
    def __init__(self) -> None:
        _lock_secret_persistence_to_this_machine()

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

    def __init__(self, data_dir: str | None = None) -> None:
        self._values: dict[str, str] = {}
        self._data_dir = data_dir
        if data_dir:
            import json
            from pathlib import Path

            path = Path(data_dir) / "e2e" / "secrets.json"
            try:
                self._values.update(json.loads(path.read_text(encoding="utf-8")))
            except (FileNotFoundError, json.JSONDecodeError):
                pass

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value
        self._persist()

    def delete(self, name: str) -> None:
        self._values.pop(name, None)
        self._persist()

    def _persist(self) -> None:
        if not self._data_dir:
            return
        import json
        from pathlib import Path

        path = Path(self._data_dir) / "e2e" / "secrets.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._values), encoding="utf-8")
