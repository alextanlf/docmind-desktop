"""Feishu token resolution on top of the unified credential store.

Two credential channels feed this module:

* ``(feishu, app)``  — keychain entry ``feishu:app-credentials`` holding
  ``"<app_id>:<app_secret>"``; exchanged for a tenant_access_token and
  cached in memory until expiry.
* ``(feishu, user)`` — keychain entry ``feishu:user-token`` holding a JSON
  blob ``{access_token, refresh_token, expires_at, name}`` produced by the
  OAuth loopback flow (``app.feishu.oauth``); refreshed on demand.

``best_token`` prefers the user token (sees everything the user sees) and
falls back to the tenant token (sees only what was shared with the app).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from app.api.errors import DomainError
from app.feishu import client
from app.remote.credentials import CredentialStore

FEISHU_APP_SECRET_REF = "feishu:app-credentials"
FEISHU_USER_SECRET_REF = "feishu:user-token"

# Renew slightly ahead of the stated expiry to avoid mid-flight expiry.
_EXPIRY_SKEW_SECONDS = 120


def parse_app_credentials(secret: str) -> tuple[str, str]:
    """Split a stored ``app_id:app_secret`` pair (``:`` or whitespace)."""
    compact = secret.strip()
    if ":" in compact:
        app_id, _, app_secret = compact.partition(":")
    else:
        parts = compact.split()
        if len(parts) != 2:
            app_id, app_secret = "", ""
        else:
            app_id, app_secret = parts
    app_id, app_secret = app_id.strip(), app_secret.strip()
    if not app_id or not app_secret:
        raise DomainError(
            "FEISHU_APP_CREDENTIALS_INVALID",
            "飞书应用凭据格式无效，应为 App ID:App Secret",
            400,
            False,
            "按 App ID:App Secret 格式重新保存",
        )
    return app_id, app_secret


@dataclass
class UserTokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: float | None
    name: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "name": self.name,
            }
        )

    @staticmethod
    def from_json(raw: str) -> "UserTokenBundle | None":
        try:
            data: Any = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        return UserTokenBundle(
            access_token=str(data["access_token"]),
            refresh_token=data.get("refresh_token") or None,
            expires_at=float(data["expires_at"]) if data.get("expires_at") else None,
            name=data.get("name") or None,
        )

    @property
    def expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at - _EXPIRY_SKEW_SECONDS


class FeishuTokenManager:
    """Resolves access tokens for the Feishu provider from stored credentials."""

    def __init__(self, credential_store: CredentialStore) -> None:
        self._store = credential_store
        self._tenant_cache: tuple[str, float] | None = None
        self._lock = asyncio.Lock()

    @property
    def credential_store(self) -> CredentialStore:
        return self._store

    # -- app channel --------------------------------------------------------

    def app_credentials(self) -> tuple[str, str] | None:
        secret = self._store.secret_for("feishu", "app")
        if not secret:
            return None
        try:
            return parse_app_credentials(secret)
        except DomainError:
            return None

    async def tenant_token(self) -> str | None:
        credentials = self.app_credentials()
        if credentials is None:
            return None
        async with self._lock:
            if self._tenant_cache is not None:
                token, expires_at = self._tenant_cache
                if time.time() < expires_at - _EXPIRY_SKEW_SECONDS:
                    return token
            app_id, app_secret = credentials
            try:
                token = await client.fetch_tenant_access_token(app_id, app_secret)
            except DomainError:
                return None
            # tenant_access_token lives 2h; cache for 1h to stay safe.
            self._tenant_cache = (token, time.time() + 3600)
            return token

    # -- user channel ---------------------------------------------------------

    def user_bundle(self) -> UserTokenBundle | None:
        secret = self._store.secret_for("feishu", "user")
        if not secret:
            return None
        return UserTokenBundle.from_json(secret)

    def save_user_bundle(self, bundle: UserTokenBundle) -> None:
        self._store.save_secret("feishu", "user", bundle.to_json(), FEISHU_USER_SECRET_REF)

    async def user_token(self) -> str | None:
        bundle = self.user_bundle()
        if bundle is None:
            return None
        if not bundle.expired:
            return bundle.access_token
        if not bundle.refresh_token:
            return None
        credentials = self.app_credentials()
        if credentials is None:
            return None
        async with self._lock:
            bundle = self.user_bundle()
            if bundle is None or not bundle.expired:
                return bundle.access_token if bundle else None
            try:
                app_token = await client.fetch_app_access_token(*credentials)
                data = await client.refresh_user_access_token(app_token, bundle.refresh_token or "")
            except DomainError:
                return None
            refreshed = UserTokenBundle(
                access_token=str(data["access_token"]),
                refresh_token=data.get("refresh_token") or bundle.refresh_token,
                expires_at=_expiry_from(data),
                name=bundle.name,
            )
            self.save_user_bundle(refreshed)
            self._store.mark_state("feishu", "user", "verified", account_label=refreshed.name)
            return refreshed.access_token

    # -- combined ---------------------------------------------------------------

    async def best_token(self) -> str | None:
        record = self._store.get("feishu", "user")
        if record is not None and record.state == "verified":
            token = await self.user_token()
            if token:
                return token
        return await self.tenant_token()

    async def require_token(self) -> str:
        token = await self.best_token()
        if token is None:
            raise DomainError(
                "FEISHU_LOGIN_REQUIRED",
                "尚未配置飞书访问凭证",
                401,
                False,
                "在设置中配置飞书自建应用或完成账号授权",
            )
        return token


def _expiry_from(data: dict[str, Any]) -> float | None:
    expires_in = data.get("expires_in") or data.get("refresh_expires_in")
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return time.time() + max(seconds - _EXPIRY_SKEW_SECONDS, 0)
