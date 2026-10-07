"""Wire models for the plugin catalogue.

Kept separate from ``app.schemas.remote`` because plugins are a *presentation
and discovery* concept layered on top of providers, not a second transport
contract. The existing provider/credential endpoints stay the write path; this
only adds a read model shaped for a searchable card grid.
"""
from __future__ import annotations

from dataclasses import field

from app.schemas.common import WireModel


class PluginManifestView(WireModel):
    """One plugin card, fully renderable without a second request."""

    # Stable "<provider>:<channel>" identity. Keyed on by the renderer and safe
    # to put in URLs / analytics later.
    id: str
    provider: str
    channel: str

    label: str
    # The integration this plugin plugs into, shown as the card subtitle.
    provider_label: str
    summary: str | None = None
    hint: str | None = None
    icon: str | None = None
    # Alias terms a user might type that the labels do not contain.
    keywords: list[str] = field(default_factory=list)
    purpose: str = "source"
    homepage: str | None = None
    version: str | None = None

    has_secret: bool = False
    secret_placeholder: str | None = None
    help_url: str | None = None
    help_label: str | None = None

    configured: bool = False
    state: str = "disconnected"
    account_label: str | None = None
    browser_install: bool = False
    browser_unavailable_code: str | None = None