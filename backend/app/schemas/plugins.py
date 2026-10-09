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


class PluginOriginView(WireModel):
    """The plugin a row belongs to, and what the page may do about it.

    Travels with every card, so the detail view can name where the plugin came
    from and decide on its own which buttons to draw — the alternative, a second
    request per row, is what the page already avoided once for credential fields.
    """

    plugin: str
    #: ``builtin`` / ``directory`` / ``distribution``. A string rather than an
    #: enum: an unfamiliar value must still render, for the same reason an
    #: unknown card kind does.
    source: str
    #: The directory, when the plugin is one. Empty for a distribution, where
    #: the location is not something the user can act on.
    path: str | None = None
    version: str | None = None
    #: False once the user switched it off; the row stays on the page.
    enabled: bool = True
    #: True when it loaded. Enabled but inactive is what a load failure looks like.
    active: bool = True
    error: str | None = None
    #: Whether a switch and a remove button belong on the detail view. Declared
    #: by the server, because only it knows where the plugin came from.
    toggleable: bool = False
    removable: bool = False


class PluginStateView(WireModel):
    """The outcome of switching a plugin off or taking it out.

    ``restart_required`` is always true and says so explicitly rather than being
    left for the UI to know: plugins are discovered once per process, so nothing
    the user just changed is live yet, and a page that stayed silent about that
    would look like a switch that did nothing.
    """

    plugin: str
    enabled: bool
    restart_required: bool = True
    #: Where a removed plugin was moved to. Reported rather than described —
    #: "moved to the trash" is not true on every platform this ships on.
    removed_to: str | None = None


class PluginToggleRequest(WireModel):
    """The switch's new position."""

    enabled: bool


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
    #: Which plugin this row belongs to. ``None`` only for a contribution
    #: installed with nothing to attribute it to, which is not a shape the
    #: application produces — the renderer treats it as "no plugin actions".
    origin: PluginOriginView | None = None
