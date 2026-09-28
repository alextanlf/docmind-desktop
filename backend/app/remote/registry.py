from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from app.api.errors import DomainError
from app.remote.provider import ProviderCapabilities, ProviderIdentity, RemoteProvider
from app.schemas.remote import ProviderCapabilitiesView, ProviderSummaryView


@dataclass
class ProviderRegistration:
    """A provider plus the runtime probe that decides whether it is usable."""

    provider: RemoteProvider
    is_configured: Callable[[], bool]
    # Fake/test providers are always considered configured so that request
    # layers never fall back to "no remote binding" during tests and e2e runs.
    always_configured: bool = False


class ProviderRegistry:
    """Central catalog of remote knowledge-base providers.

    Replaces the former ``isinstance(gateway, RoutingYuqueGateway)`` probing:
    consumers ask the registry which providers are configured instead of
    sniffing concrete implementation types.
    """

    def __init__(self) -> None:
        self._registrations: dict[str, ProviderRegistration] = {}

    def register(
        self,
        provider: RemoteProvider,
        is_configured: Callable[[], bool] | None = None,
        *,
        always_configured: bool = False,
    ) -> None:
        name = _provider_name(provider)
        if name in self._registrations:
            raise ValueError(f"remote provider already registered: {name}")
        if is_configured is None and not always_configured:
            is_configured = lambda: False  # noqa: E731
        self._registrations[name] = ProviderRegistration(
            provider=provider,
            is_configured=is_configured,
            always_configured=always_configured,
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
        except Exception:
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
