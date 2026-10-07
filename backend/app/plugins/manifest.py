"""The plugin card: one self-declared piece of the settings page.

A *plugin* is an optional capability the user installs on top of DocMind, and
the reason the layer exists is size: anything most users do not need should not
be in the installer. A remote knowledge base is one kind of such a capability;
a document format is another. The card is how any of them is presented, so its
fields are the union of what the kinds need — and nothing here names a vendor
or a plugin, because every string is declared by the plugin itself.

``kind`` is the one field the renderer acts on, and it selects a card *body*
(a credential form, a login button, a static description), not a vendor.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.plugins import PluginManifestView


@dataclass(frozen=True)
class PluginManifest:
    """Everything needed to render and find one plugin card.

    ``id`` is the stable identity of the card: ``"<provider>:<channel>"`` for a
    remote source, ``"<plugin>:core"`` for a contribution that has no credential
    channel. It is what the renderer keys on and what deep links / future
    per-plugin settings will address, so it must not change when a label or icon
    does.
    """

    id: str
    #: Which kind of capability this is. Declared by the contribution, and the
    #: only thing the renderer dispatches on when choosing a card body.
    kind: str
    provider: str
    channel: str

    # Card title. Defaults to the channel label; a contribution may override it
    # when the channel label alone would not make sense as a standalone title.
    label: str
    # Which integration this belongs to, shown as the card's subtitle. A card
    # has to say what it plugs into — a bare 「机器人」 tells the user nothing —
    # but it must not group the page: no ordering by vendor, no per-vendor
    # headings, nothing that implies these are alternatives to each other rather
    # than independent add-ons. ``None`` for a contribution that plugs into
    # nothing (a format), where a subtitle would be noise.
    provider_label: str | None = None
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
    # The category tag's *display text* ("知识库" / "通知" / "文档格式"), declared
    # rather than derived from `purpose`. Deriving it in the renderer meant the
    # page had to know what each purpose was called, which is copy the plugin
    # owns. Shown as a tag on the card — metadata, not a grouping: the page does
    # not sort or partition on it.
    tag: str | None = None
    homepage: str | None = None
    version: str | None = None
    # Suffixes this contribution adds, for a format card ("支持 .tex"). Only
    # meaningful for `document_format`; empty otherwise.
    extensions: tuple[str, ...] = ()

    # Credential presentation, copied from the channel spec so the card renders
    # from the manifest alone (the renderer would otherwise need a second
    # request per plugin just to learn whether to draw a login button or a
    # secret field). Not applicable to contributions without credentials.
    has_secret: bool = False
    secret_placeholder: str | None = None
    help_url: str | None = None
    help_label: str | None = None

    # Live credential state. Credential-specific: a contribution that stores no
    # secret leaves these at their defaults, and the renderer draws no status
    # badge for such a card.
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
            kind=self.kind,
            provider=self.provider,
            channel=self.channel,
            label=self.label,
            provider_label=self.provider_label,
            summary=self.summary,
            hint=self.hint,
            icon=self.icon,
            keywords=list(self.keywords),
            tag=self.tag,
            homepage=self.homepage,
            version=self.version,
            extensions=list(self.extensions),
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


def merge_keywords(*groups: tuple[str, ...] | object) -> tuple[str, ...]:
    """Concatenate keyword groups, dropping case-insensitive duplicates.

    A provider's own name is prepended to every manifest's keywords, and a
    provider whose ``keywords`` already mention its name (a natural thing to
    write) would otherwise ship that term twice. Duplicates are invisible in the
    UI but leak into the search haystack, where they cost a redundant ``in``
    check per keystroke and make the manifest harder to assert on.

    Order is preserved and the first spelling wins, so a channel's own aliases
    keep priority over the provider-wide ones.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for keyword in group or ():  # type: ignore[union-attr]
            text = str(keyword)
            folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            merged.append(text)
    return tuple(merged)
