from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from app.api.errors import DomainError
from app.remote.credentials import CredentialStore, ProviderCredentialSpec
from app.remote.provider import ProviderCapabilities, RemoteProvider
from app.schemas.remote import ProviderCapabilitiesView, ProviderSummaryView


@dataclass
class ProviderRegistration:
    """A provider plus the runtime probe that decides whether it is usable."""

    provider: RemoteProvider
    is_configured: Callable[[], bool]
    # Fake/test providers are always considered configured so that request
    # layers never fall back to "no remote binding" during tests and e2e runs.
    always_configured: bool = False
    credential_spec: ProviderCredentialSpec | None = None


class ProviderRegistry:
    """Central catalog of remote knowledge-base providers.

    Replaces the former ``isinstance(gateway, RoutingYuqueGateway)`` probing:
    consumers ask the registry which providers are configured instead of
    sniffing concrete implementation types.

    When a ``CredentialStore`` is attached and a provider registers with a
    ``credential_spec`` but no explicit probe, the default probe is simply
    "any declared channel verified" — no hand-written per-provider closures.
    """

    def __init__(self, credential_store: CredentialStore | None = None) -> None:
        self._registrations: dict[str, ProviderRegistration] = {}
        self._credential_store = credential_store

    def register(
        self,
        provider: RemoteProvider,
        is_configured: Callable[[], bool] | None = None,
        *,
        always_configured: bool = False,
        credential_spec: ProviderCredentialSpec | None = None,
    ) -> None:
        name = _provider_name(provider)
        if name in self._registrations:
            raise ValueError(f"remote provider already registered: {name}")
        if is_configured is None and not always_configured:
            if credential_spec is not None and self._credential_store is not None:
                store = self._credential_store
                channels = tuple(channel.name for channel in credential_spec.channels)
                # Only channels the provider itself declares count — e.g. a
                # Feishu *webhook* binding must not make the Feishu
                # knowledge-base provider look configured.
                is_configured = lambda: store.any_verified(name, channels)
            else:
                is_configured = lambda: False
        self._registrations[name] = ProviderRegistration(
            provider=provider,
            is_configured=is_configured,
            always_configured=always_configured,
            credential_spec=credential_spec,
        )

    def get(self, name: str) -> RemoteProvider:
        try:
            return self._registrations[name].provider
        except KeyError:
            known = ", ".join(sorted(self._registrations)) or "none"
            raise DomainError(
                "REMOTE_PROVIDER_UNKNOWN",
                f"未知的远程知识库来源：{name}",
                404,
                False,
                f"可用的来源：{known}",
            ) from None

    def credential_spec(self, name: str) -> ProviderCredentialSpec | None:
        registration = self._registrations.get(name)
        return registration.credential_spec if registration else None

    def names(self) -> list[str]:
        return sorted(self._registrations)

    def is_configured(self, name: str) -> bool:
        registration = self._registrations.get(name)
        if registration is None:
            return False
        if registration.always_configured:
            return True
        try:
            return bool(registration.is_configured())
        except Exception:  # noqa: BLE001 - a failing probe means "not configured"
            return False

    def any_configured(self) -> bool:
        return any(self.is_configured(name) for name in self._registrations)

    def first_configured(self) -> RemoteProvider | None:
        for name in sorted(self._registrations):
            if self.is_configured(name):
                return self._registrations[name].provider
        return None

    def summaries(self) -> list[ProviderSummaryView]:
        views: list[ProviderSummaryView] = []
        for name in self.names():
            registration = self._registrations[name]
            capabilities = _capabilities_of(registration.provider)
            views.append(
                ProviderSummaryView(
                    name=name,
                    label=_label_of(registration.provider, name),
                    configured=self.is_configured(name),
                    capabilities=ProviderCapabilitiesView(
                        browser_install=capabilities.browser_install,
                        marker_lookup=capabilities.marker_lookup,
                        parent_node_write=capabilities.parent_node_write,
                    ),
                )
            )
        return views

    async def close_all(self) -> None:
        await asyncio.gather(
            *(registration.provider.close() for registration in self._registrations.values()),
            return_exceptions=True,
        )


def _provider_name(provider: RemoteProvider) -> str:
    identity = getattr(provider, "identity", None)
    if identity is not None:
        return identity.name
    name = getattr(provider, "name", None)
    if isinstance(name, str) and name:
        return name
    raise ValueError("remote provider must expose identity.name")


def _capabilities_of(provider: RemoteProvider) -> ProviderCapabilities:
    identity = getattr(provider, "identity", None)
    if identity is not None:
        return identity.capabilities
    return ProviderCapabilities()


def _label_of(provider: RemoteProvider, fallback: str) -> str:
    identity = getattr(provider, "identity", None)
    if identity is not None and identity.label:
        return identity.label
    return fallback
