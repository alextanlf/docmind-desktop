"""Derives the plugin catalogue from the provider registry.

There is exactly one source of truth: whatever is registered in
``ProviderRegistry``. A plugin card is not stored, configured or listed
anywhere — it is computed per request from the provider's identity, its
credential channels, and the live credential state. So a newly installed
third-party plugin appears in the settings page without a single line changing
in this file or in the renderer.
"""
from __future__ import annotations

from app.plugins.manifest import PluginManifest
from app.remote.credentials import CredentialStore
from app.remote.provider import ProviderCapabilities
from app.remote.registry import ProviderRegistry


class PluginCatalog:
    """Read model over ``ProviderRegistry`` + ``CredentialStore``."""

    def __init__(self, registry: ProviderRegistry, credential_store: CredentialStore) -> None:
        self.registry = registry
        self.credential_store = credential_store

    def manifests(self) -> list[PluginManifest]:
        """One manifest per credential channel, in registration order.

        Providers that declare no credential spec still deserve a card (a plugin
        whose login needs no credential at all), so they contribute a single
        manifest keyed by the ``core`` channel rather than disappearing.
        """
        manifests: list[PluginManifest] = []
        for name in self.registry.names():
            provider = self.registry.get(name)
            identity = getattr(provider, "identity", None)
            provider_label = getattr(identity, "label", None) or name
            spec = self.registry.credential_spec(name)
            if spec is None:
                manifests.append(self._synthetic_manifest(name, provider_label))
                continue
            for channel in spec.channels:
                manifests.append(self._channel_manifest(name, provider_label, channel))
        return manifests

    def search(self, query: str) -> list[PluginManifest]:
        """Filter manifests by a free-text query.

        Matching is done here rather than in the renderer so the rule is
        testable and identical everywhere: every whitespace-separated term must
        match somewhere in the plugin's searchable text, case- and
        accent-insensitively via plain lowercasing. An empty query returns
        everything, which is what the page shows before the user types.
        """
        manifests = self.manifests()
        terms = [term for term in query.strip().lower().split() if term]
        if not terms:
            return manifests
        return [m for m in manifests if _matches(m, terms)]

    # -- internals --------------------------------------------------------

    def _channel_manifest(self, provider: str, provider_label: str, channel) -> PluginManifest:
        state = self.credential_store.channel_state(provider, channel.name, channel)
        capabilities = self._capabilities(provider)
        return PluginManifest(
            id=f"{provider}:{channel.name}",
            provider=provider,
            channel=channel.name,
            label=channel.label,
            provider_label=provider_label,
            summary=channel.summary,
            hint=channel.hint,
            icon=channel.icon or getattr(self._identity(provider), "icon", None),
            keywords=tuple(channel.keywords) + self._identity_keywords(provider),
            purpose=channel.purpose,
            homepage=getattr(self._identity(provider), "homepage", None),
            version=getattr(self._identity(provider), "version", None),
            has_secret=channel.has_secret,
            secret_placeholder=channel.secret_placeholder,
            help_url=channel.help_url,
            help_label=channel.help_label,
            configured=state.configured,
            state=state.state,
            account_label=state.account_label,
            browser_install=capabilities.browser_install,
            browser_unavailable_code=capabilities.browser_unavailable_code,
        )

    def _synthetic_manifest(self, provider: str, provider_label: str) -> PluginManifest:
        """A provider with no credential channels still gets one card.

        Without this a plugin whose integration needs no stored secret would be
        invisible in settings despite being fully functional — and the user
        would have no way to log into it from the UI.
        """
        capabilities = self._capabilities(provider)
        identity = self._identity(provider)
        return PluginManifest(
            id=f"{provider}:core",
            provider=provider,
            channel="core",
            label=getattr(identity, "label", None) or provider,
            provider_label=provider_label,
            summary=getattr(identity, "summary", None),
            icon=getattr(identity, "icon", None),
            keywords=self._identity_keywords(provider),
            homepage=getattr(identity, "homepage", None),
            version=getattr(identity, "version", None),
            browser_install=capabilities.browser_install,
            browser_unavailable_code=capabilities.browser_unavailable_code,
        )

    def _identity(self, provider: str):
        """The provider's declared metadata, by name.

        Takes a name rather than the object because that is what every caller
        has: a provider object would make ``getattr(obj, "identity")`` the
        obvious spelling, which then silently yields ``None`` the moment a
        caller passes the name it already has — and the card quietly loses its
        icon, summary and keywords instead of raising.
        """
        return getattr(self.registry.get(provider), "identity", None)

    def _identity_keywords(self, provider: str) -> tuple[str, ...]:
        identity = self._identity(provider)
        keywords = getattr(identity, "keywords", ()) if identity is not None else ()
        # The provider's own name is always searchable: "yuque" must find 语雀
        # even though no label contains the latin spelling.
        return (provider,) + tuple(keywords)

    def _capabilities(self, provider: str):
        identity = self._identity(provider)
        capabilities = getattr(identity, "capabilities", None) if identity is not None else None
        # A plugin may expose no capabilities view at all (the protocol only
        # requires ``identity.name``). Falling back to the neutral default keeps
        # the manifest builder total instead of AttributeError-ing on a
        # half-implemented third-party plugin.
        return capabilities if capabilities is not None else ProviderCapabilities()


def _matches(manifest: PluginManifest, terms: list[str]) -> bool:
    haystack = " ".join(
        part
        for part in (
            manifest.label,
            manifest.provider_label,
            manifest.summary or "",
            manifest.provider,
            manifest.channel,
            " ".join(manifest.keywords),
        )
        if part
    ).lower()
    return all(term in haystack for term in terms)