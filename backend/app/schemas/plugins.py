"""Wire models for the plugin catalogue.

Kept separate from ``app.schemas.remote`` because plugins are a *presentation
and discovery* concept layered on top of what they contribute, not a second
transport contract. The provider/credential endpoints stay the write path; this
only adds a read model shaped for a searchable card grid.
"""
from __future__ import annotations

from dataclasses import field

from app.schemas.common import WireModel


class PluginDirectoryView(WireModel):
    """Where plugins are looked for.

    Reported by the server rather than documented, because it is derived from the
    application's data directory — which differs per platform and follows the
    application's own name. A path written into a guide goes stale the first time
    either changes, and the user is left cloning into a directory nothing reads.
    """

    path: str


class PluginManifestView(WireModel):
    """One plugin card, fully renderable without a second request."""

    # Stable "<provider>:<channel>" identity. Keyed on by the renderer and safe
    # to put in URLs / analytics later.
    id: str
    #: What kind of capability this card configures. The renderer picks a card
    #: *body* from it (credential form / login / static description) and draws a
    #: status badge only where a credential is involved. It selects a body, not
    #: a vendor, and an unknown value falls back to a neutral card.
    kind: str
    provider: str
    channel: str

    label: str
    # The integration this card plugs into, shown as the subtitle. ``None`` for
    # a contribution that plugs into nothing, where a subtitle is noise.
    provider_label: str | None = None
    summary: str | None = None
    hint: str | None = None
    icon: str | None = None
    # Alias terms a user might type that the labels do not contain.
    keywords: list[str] = field(default_factory=list)
    # The category tag's display text ("知识库" / "通知" / "文档格式"), declared
    # by the plugin. Rendered as a tag; never used to group the page.
    tag: str | None = None
    homepage: str | None = None
    version: str | None = None
    # Suffixes this card adds ("支持 .tex"); empty for anything but a format.
    extensions: list[str] = field(default_factory=list)

    has_secret: bool = False
    secret_placeholder: str | None = None
    help_url: str | None = None
    help_label: str | None = None

    configured: bool = False
    state: str = "disconnected"
    account_label: str | None = None
    browser_install: bool = False
    browser_unavailable_code: str | None = None
