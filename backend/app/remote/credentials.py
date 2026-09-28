"""Unified provider credential state.

Replaces the scattered ``settings``-key based credential state
(``yuque-api.verified`` / ``yuque-web.connected`` ...) with a single
``provider_credentials`` table plus declarative per-provider channel specs.

Design rules:

* Secrets stay in the platform keychain (``SecretStore``); the table only
  stores non-secret state and the keychain entry name (``secret_ref``).
* Each provider *declares* its channels via ``ProviderCredentialSpec`` at
  registration time; the registry's default ``is_configured`` probe is then
  "any channel verified" and never hand-written per provider.
* Legacy settings keys are only read as a fallback during the transition
  window (migration 0017 backfills them into the table).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from app.core.secrets import SecretStore
from app.storage.database import Database
from app.storage.models import ProviderCredentialRecord, ProviderCredentialState, utc_now

# A credential tester receives the stored secret and returns the remote
# account label on success, or raises DomainError on failure.
CredentialTester = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class CredentialChannelSpec:
    """Declares one credential channel of a provider (e.g. yuque ``api``)."""

    name: str  # 'api' | 'web' | 'webhook' | ...
    label: str  # display name, e.g. "语雀 API"
    has_secret: bool  # whether a keychain entry backs this channel
    default_secret_ref: str | None = None  # keychain entry name when has_secret
    tester: CredentialTester | None = None  # verification probe for save/test flows


@dataclass(frozen=True)
class ProviderCredentialSpec:
    provider: str
    channels: tuple[CredentialChannelSpec, ...]
    # Channel whose state tracks the interactive login flow (/login + /status).
    # Yuque uses the browser-session ``web`` channel; Feishu maps its OAuth
    # flow onto the ``user`` channel instead.
    login_channel: str = "web"

    def channel(self, name: str) -> CredentialChannelSpec | None:
        for spec in self.channels:
            if spec.name == name:
                return spec
        return None


@dataclass(frozen=True)
class CredentialChannelState:
    """Public, secret-free view of one channel's credential state."""

    provider: str
    channel: str
    configured: bool
    state: str  # 'verified' | 'unverified' | 'disconnected'
    account_label: str | None
    has_secret: bool
    label: str

    @property
    def verified(self) -> bool:
        return self.state == ProviderCredentialState.VERIFIED.value


class CredentialStore:
    """CRUD over ``provider_credentials`` + the backing keychain entries."""

    def __init__(self, database: Database, secret_store: SecretStore) -> None:
        self.database = database
        self.secret_store = secret_store

    # -- reads ------------------------------------------------------------

    def get(self, provider: str, channel: str) -> ProviderCredentialRecord | None:
        with self.database.session() as session:
            return session.get(ProviderCredentialRecord, (provider, channel))

    def list_by_provider(self, provider: str) -> list[ProviderCredentialRecord]:
        with self.database.session() as session:
            return list(
                session.query(ProviderCredentialRecord)
                .filter(ProviderCredentialRecord.provider == provider)
                .order_by(ProviderCredentialRecord.channel)
            )

    def any_verified(self, provider: str, channels: tuple[str, ...] | None = None) -> bool:
        with self.database.session() as session:
            query = session.query(ProviderCredentialRecord).filter(
                ProviderCredentialRecord.provider == provider,
                ProviderCredentialRecord.state == ProviderCredentialState.VERIFIED.value,
            )
            if channels is not None:
                query = query.filter(ProviderCredentialRecord.channel.in_(channels))
            return query.count() > 0

    def secret_for(self, provider: str, channel: str) -> str | None:
        record = self.get(provider, channel)
        if record is None or record.secret_ref is None:
            return None
        try:
            return self.secret_store.get(record.secret_ref)
        except Exception:  # noqa: BLE001 - keychain access is best-effort here
            return None

    def channel_state(
        self, provider: str, channel: str, spec: CredentialChannelSpec | None = None
    ) -> CredentialChannelState:
        record = self.get(provider, channel)
        has_secret = bool(spec.has_secret) if spec else bool(record and record.secret_ref)
        configured = False
        if spec and spec.has_secret:
            secret_ref = (record.secret_ref if record else None) or spec.default_secret_ref
            configured = bool(secret_ref and self._safe_secret_get(secret_ref))
        elif record is not None:
            configured = record.state != ProviderCredentialState.DISCONNECTED.value
        return CredentialChannelState(
            provider=provider,
            channel=channel,
            configured=configured,
            state=record.state if record else ProviderCredentialState.DISCONNECTED.value,
            account_label=record.account_label if record else None,
            has_secret=has_secret,
            label=spec.label if spec else channel,
        )

    # -- writes -----------------------------------------------------------

    def save_secret(self, provider: str, channel: str, value: str, secret_ref: str) -> None:
        """Store a secret and reset the channel to ``unverified``."""
        previous = self.secret_store.get(secret_ref)
        if value:
            self.secret_store.set(secret_ref, value)
        else:
            self.secret_store.delete(secret_ref)
        try:
            self._upsert(
                provider,
                channel,
                state=ProviderCredentialState.UNVERIFIED.value,
                account_label=None,
                secret_ref=secret_ref,
            )
        except Exception:
            # Roll the keychain write back if the state row could not persist.
            if previous is None:
                self.secret_store.delete(secret_ref)
            else:
                self.secret_store.set(secret_ref, previous)
            raise

    def mark_state(
        self,
        provider: str,
        channel: str,
        state: str,
        account_label: str | None = None,
    ) -> None:
        self._upsert(provider, channel, state=state, account_label=account_label)

    def clear(self, provider: str, channel: str) -> None:
        """Remove the keychain entry and mark the channel disconnected."""
        record = self.get(provider, channel)
        if record is not None and record.secret_ref:
            self.secret_store.delete(record.secret_ref)
        self._upsert(
            provider,
            channel,
            state=ProviderCredentialState.DISCONNECTED.value,
            account_label=None,
        )

    # -- internals --------------------------------------------------------

    def _safe_secret_get(self, secret_ref: str) -> str | None:
        try:
            return self.secret_store.get(secret_ref)
        except Exception:  # noqa: BLE001 - keychain access is best-effort here
            return None

    def _upsert(
        self,
        provider: str,
        channel: str,
        *,
        state: str,
        account_label: str | None,
        secret_ref: str | None = None,
    ) -> None:
        with self.database.session() as session:
            record = session.get(ProviderCredentialRecord, (provider, channel))
            if record is None:
                session.add(
                    ProviderCredentialRecord(
                        provider=provider,
                        channel=channel,
                        state=state,
                        account_label=account_label,
                        secret_ref=secret_ref,
                    )
                )
            else:
                record.state = state
                record.account_label = account_label
                if secret_ref is not None:
                    record.secret_ref = secret_ref
                record.updated_at = utc_now()
