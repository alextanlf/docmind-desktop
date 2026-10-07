"""The plugin manifest: one card's worth of self-declared metadata.

A *plugin*, as the settings page presents it, is **one credential channel of
one provider** — not the provider. That granularity is deliberate: 「语雀网页
登录」 and 「语雀 API」 are two different things a user chooses between (open a
browser vs paste a token), and collapsing them hides that choice. Likewise
「飞书账号授权」 and 「飞书机器人」 are not two features of one thing.

Nothing here names a vendor. Every string a card renders — title, summary,
icon, search keywords — is declared by the plugin itself, so installing a
third-party plugin needs no change to the API layer or the renderer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.plugins import PluginManifestView


@dataclass(frozen=True)
class PluginManifest:
    """Everything needed to render and find one plugin card.

    ``id`` is the stable identity of the card: ``"<provider>:<channel>"``. It is
    what the renderer keys on and what deep links / future per-plugin settings
    will address, so it must not change when a label or icon does.
    """

    id: str
    provider: str
    channel: str

    # Card title. Defaults to the channel label; a provider may override it when
    # the channel label alone would not make sense as a standalone card title.
    label: str
    # Which integration this belongs to, shown as the card's subtitle. A plugin
    # card has to say what it plugs into — a bare 「机器人」 tells the user
    # nothing — but it must not group the page: no ordering by vendor, no
    # per-vendor headings, nothing that implies these are alternatives to each
    # other rather than independent add-ons.
    provider_label: str
    # What the plugin does, in one line. Shown as the card body.
    summary: str | None = None
    # What choosing it costs ("会在导入时打开 Chrome", "需要先申请令牌").
    hint: str | None = None
    # Icon key resolved by the renderer's glyph table.
    icon: str | None = None
    # Search terms. The renderer also searches label / provider_label / summary,
    # so this is only for aliases a user would type but cannot guess
    # ("wiki" for 语雀, "Lark" for 飞书).
    keywords: tuple[str, ...] = ()
    # "source" (provides documents) / "notify" (pushes results to a chat).
    # Declared by the plugin and rendered as a tag — it is metadata, not a
    # grouping: the page does not sort or partition on it.
    purpose: str = "source"
    homepage: str | None = None
    version: str | None = None

    # Credential presentation, copied from the channel spec so the card can be
    # rendered from the manifest alone (the renderer would otherwise need a
    # second request per plugin just to learn whether to draw a login button or
    # a secret field).
    has_secret: bool = False
    secret_placeholder: str | None = None
    help_url: str | None = None
    help_label: str | None = None

    # Live credential state for this channel.
    configured: bool = False
    state: str = "disconnected"
    account_label: str | None = None
    # Whether the login browser install affordance applies, plus the error code
    # its absence raises. Declared, never matched by vendor name.
    browser_install: bool = False
    browser_unavailable_code: str | None = None

    def view(self) -> PluginManifestView:
        return PluginManifestView(
            id=self.id,
            provider=self.provider,
            channel=self.channel,
            label=self.label,
            provider_label=self.provider_label,
            summary=self.summary,
            hint=self.hint,
            icon=self.icon,
            keywords=list(self.keywords),
            purpose=self.purpose,
            homepage=self.homepage,
            version=self.version,
            has_secret=self.has_secret,
            secret_placeholder=self.secret_placeholder,
            help_url=self.help_url,
            help_label=self.help_label,
            configured=self.configured,
            state=self.state,
            account_label=self.account_label,
            browser_install=self.browser_install,
            browser_unavailable_code=self.browser_unavailable_code,
        )


@dataclass
class PluginContribution:
    """What a plugin package hands back at registration time.

    This is the extension seam for third-party plugins. A plugin author
    implements ``RemoteProvider``, declares a ``ProviderCredentialSpec``, and
    returns one of these from their entry point. DocMind never imports their
    module by name.

    ``notifications`` is optional and defaults to empty: pushing import results
    somewhere is a capability of a plugin, not a requirement, so a plugin that
    only reads documents does not have to implement it.
    """

    provider: object
    credential_spec: object
    is_configured: object | None = None
    notifications: tuple[object, ...] = field(default=())