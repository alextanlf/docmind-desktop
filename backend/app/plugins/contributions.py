"""What a plugin adds to DocMind.

A *contribution* is one optional capability. The plugin layer exists to keep
non-essential capabilities out of the installer, so "remote knowledge base" is
not the layer's definition — it is one kind among several. A document format is
another, and a notification destination a third.

Two methods make the layer extensible without a dispatch table anywhere:

* ``install(host)`` puts the capability where it belongs (a provider registry, a
  format registry, a notification hub). The loader calls it and never asks what
  kind it is, so adding a kind means adding a class — not editing the loader.
* ``cards(host)`` describes the capability for the settings page. The catalogue
  asks every contribution for its cards and concatenates, so it also never
  branches on kind.

That is the whole design: **polymorphism instead of a switch**. The previous
shape could only be satisfied by a remote provider with its twelve methods and a
credential spec, which is why "add a format plugin" had no answer.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

from app.document.formats import DocumentFormat, FormatRegistry
from app.plugins.manifest import PluginManifest, merge_keywords
from app.plugins.records import SOURCE_BUILTIN, PluginRecord
from app.remote.credentials import CredentialStore, ProviderCredentialSpec
from app.remote.notifications import NotificationHub, NotificationTarget
from app.remote.provider import ProviderIdentity, RemoteProvider
from app.remote.registry import ProviderRegistry

#: Kind identifiers. Wire-visible: the renderer picks a card body by kind, so
#: these are part of the contract rather than an internal detail. Declared next
#: to the class that owns them so a kind cannot be half-introduced.
KIND_REMOTE_SOURCE = "remote_source"
KIND_DOCUMENT_FORMAT = "document_format"
#: Not a capability. A plugin that contributes no card right now — switched off,
#: or failed to load — is still a row on the page, because a plugin that
#: disappeared when it was switched off could not be switched back on. The
#: renderer draws no body for it: there is nothing to configure until it loads.
KIND_PLUGIN = "plugin"


@dataclass
class PluginHost:
    """Where contributions install themselves, and what they may read.

    A plain container rather than a protocol, so ``install`` stays polymorphic:
    a contribution reaches for the registry it needs by name instead of the
    loader deciding on its behalf.
    """

    providers: ProviderRegistry
    formats: FormatRegistry
    credentials: CredentialStore
    notifications: NotificationHub | None = None
    #: Every plugin that installed successfully, in install order. The catalogue
    #: reads this, so a plugin that failed to install contributes no card — a
    #: half-registered plugin must not look installed. A plugin with no cards at
    #: all is here too: it is still something the user installed, switched off or
    #: needs to fix.
    plugins: list[PluginRecord] = field(default_factory=list)

    @property
    def contributions(self) -> list[object]:
        """Every installed contribution, in install order.

        Derived from the plugins rather than tracked beside them: which
        contribution belongs to which plugin is exactly what the settings page
        has to show, so two lists would be two answers to one question.
        """
        return [
            contribution for record in self.plugins for contribution in record.contributions
        ]


@dataclass(frozen=True)
class RemoteSourceContribution:
    """A remote knowledge base: a provider plus its credential channels."""

    kind: ClassVar[str] = KIND_REMOTE_SOURCE

    provider: RemoteProvider
    credential_spec: ProviderCredentialSpec | None = None
    is_configured: Callable[[], bool] | None = None
    always_configured: bool = False
    notifications: tuple[NotificationTarget, ...] = ()

    def install(self, host: PluginHost) -> None:
        host.providers.register(
            self.provider,
            self.is_configured,
            always_configured=self.always_configured,
            credential_spec=self.credential_spec,
        )
        if host.notifications is None:
            return
        for target in self.notifications:
            host.notifications.register(target)

    def cards(self, host: PluginHost) -> list[PluginManifest]:
        identity = self.provider.identity
        provider = identity.name
        provider_label = identity.label or provider
        if self.credential_spec is None:
            # A provider whose integration needs no stored secret would
            # otherwise be invisible in settings despite being fully
            # functional — and the user could not log into it from the UI.
            return [_core_card(self.kind, provider, provider_label, identity)]
        return [
            _channel_card(self.kind, provider, provider_label, identity, channel, host)
            for channel in self.credential_spec.channels
        ]


@dataclass(frozen=True)
class DocumentFormatContribution:
    """One importable document format, as an installable capability.

    The format parses bytes; this adds the presentation the settings page needs
    and the identity that keeps two plugins from claiming one suffix. A plugin
    adding several formats returns several of these — one card each, for the
    same reason a provider's channels get one card each.
    """

    kind: ClassVar[str] = KIND_DOCUMENT_FORMAT

    format: DocumentFormat
    label: str
    summary: str | None = None
    hint: str | None = None
    icon: str | None = None
    keywords: tuple[str, ...] = ()
    tag: str | None = None
    homepage: str | None = None
    version: str | None = None
    #: The plugin's own name, used as the card's namespace. Defaults to the
    #: format name, which is right for the common case of one format per plugin.
    name: str | None = None

    def install(self, host: PluginHost) -> None:
        # Registration is what rejects a conflict; the loader records the
        # failure. A format that silently did nothing would be indistinguishable
        # from a plugin that was never installed.
        host.formats.register(self.format)

    def cards(self, host: PluginHost) -> list[PluginManifest]:
        name = self.name or self.format.name
        return [
            PluginManifest(
                id=f"{name}:core",
                kind=self.kind,
                provider=name,
                channel="core",
                label=self.label,
                # A format plugs into nothing, so there is no owner to name —
                # a subtitle here would be noise rather than orientation.
                provider_label=None,
                summary=self.summary,
                hint=self.hint,
                icon=self.icon,
                keywords=merge_keywords((name,), self.keywords),
                tag=self.tag,
                homepage=self.homepage,
                version=self.version,
                extensions=self.format.extensions,
            )
        ]


def install_contribution(
    host: PluginHost, contribution: object, *, plugin: PluginRecord | None = None
) -> None:
    """Install one contribution and record it against the plugin it came from.

    Recording happens only after ``install`` succeeds, which is what keeps a
    half-registered plugin from rendering a card for something that does not
    work.

    ``plugin`` is how the page learns where a card came from, and so which
    switch and remove button belong beside it. A caller with no plugin to name —
    a test, or a host that installs a single contribution directly — gets an
    unnamed record rather than no record at all: the card still appears, it
    simply offers no plugin-level action, which beats guessing what it was part
    of.
    """
    install = getattr(contribution, "install", None)
    cards = getattr(contribution, "cards", None)
    if not callable(install) or not callable(cards):
        raise TypeError(
            f"{type(contribution).__name__} is not a plugin contribution; "
            "expected an object with install() and cards()"
        )
    install(host)
    record = plugin if plugin is not None else PluginRecord(name="", source=SOURCE_BUILTIN)
    if all(existing is not record for existing in host.plugins):
        host.plugins.append(record)
    record.contributions.append(contribution)


# -- card construction -------------------------------------------------------
#
# Shared by the contribution kinds that render a credential channel, so the
# rules about icons, keywords and live state live in one place.


def _core_card(
    kind: str, provider: str, provider_label: str, identity: ProviderIdentity
) -> PluginManifest:
    return PluginManifest(
        id=f"{provider}:core",
        kind=kind,
        provider=provider,
        channel="core",
        label=identity.label or provider,
        provider_label=provider_label,
        summary=identity.summary,
        icon=identity.icon,
        keywords=merge_keywords((provider,), identity.keywords),
        tag=identity.tag,
        homepage=identity.homepage,
        version=identity.version,
        browser_install=identity.capabilities.browser_install,
        browser_unavailable_code=identity.capabilities.browser_unavailable_code,
    )


def _channel_card(
    kind: str,
    provider: str,
    provider_label: str,
    identity: ProviderIdentity,
    channel,
    host: PluginHost,
) -> PluginManifest:
    state = host.credentials.channel_state(provider, channel.name, channel)
    return PluginManifest(
        id=f"{provider}:{channel.name}",
        kind=kind,
        provider=provider,
        channel=channel.name,
        label=channel.label,
        provider_label=provider_label,
        summary=channel.summary,
        hint=channel.hint,
        icon=channel.icon or identity.icon,
        keywords=merge_keywords(channel.keywords, (provider,), identity.keywords),
        tag=channel.tag,
        homepage=identity.homepage,
        version=identity.version,
        has_secret=channel.has_secret,
        secret_placeholder=channel.secret_placeholder,
        help_url=channel.help_url,
        help_label=channel.help_label,
        configured=state.configured,
        state=state.state,
        account_label=state.account_label,
        browser_install=identity.capabilities.browser_install,
        browser_unavailable_code=identity.capabilities.browser_unavailable_code,
    )
