"""One plugin, as the settings page needs to see it.

A *plugin* is the unit a user installs, switches off and removes: a distribution
found through an entry point, a directory kept in the plugin directory, or one of
DocMind's own built-in integrations. A *contribution* is what it adds (see
``contributions.py``); one plugin may produce several, which is why the page
shows a card per contribution and a provenance line naming the one they came
from.

The record exists because a card cannot answer the questions the page has to:
where did this come from, is it on, and may the user change that. Every card in
the catalogue carries the ``PluginOrigin`` of the record it came from, and a
plugin with no cards at all still produces a row from its own record — otherwise
switching one off would make it vanish, and "switched off" would be
indistinguishable from "never installed".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.plugins import PluginOriginView

#: Where a plugin came from. Wire-visible: the page picks both its provenance
#: line and which actions it offers from this, so it is part of the contract.
SOURCE_BUILTIN = "builtin"
SOURCE_DIRECTORY = "directory"
SOURCE_DISTRIBUTION = "distribution"


@dataclass(frozen=True)
class PluginOrigin:
    """The plugin behind one row, and what the page may do about it."""

    plugin: str
    source: str
    #: The directory, when the plugin is one. Empty for a distribution: where
    #: ``pip`` put it is not a place a user can act on.
    path: str | None = None
    version: str | None = None
    #: False once the user switched it off. The row stays on the page.
    enabled: bool = True
    #: True when the plugin loaded and its contributions are live. A plugin can
    #: be enabled and still not active — that is what a load failure is.
    active: bool = True
    #: Why it is not active. Empty for anything that loaded.
    error: str | None = None
    #: Whether the user may switch it off, and whether they may take it out.
    #: A built-in is part of the application, so neither. An installed
    #: distribution can be switched off but not removed: the bundled runtime
    #: ships no installer to remove it with, and offering a button that cannot
    #: work is worse than the button being absent.
    toggleable: bool = False
    removable: bool = False

    def view(self) -> PluginOriginView:
        """The wire shape, built explicitly.

        Not left to the response model's coercion: pydantic does not accept a
        stdlib dataclass as a nested model without ``from_attributes``, and
        turning that on globally would loosen validation for every other
        payload in the application to accommodate one field.
        """
        return PluginOriginView(
            plugin=self.plugin,
            source=self.source,
            path=self.path,
            version=self.version,
            enabled=self.enabled,
            active=self.active,
            error=self.error,
            toggleable=self.toggleable,
            removable=self.removable,
        )


@dataclass
class PluginRecord:
    """Everything known about one plugin, whether or not it loaded.

    ``label``/``summary``/``version``/``homepage`` are the plugin's own declared
    metadata, and they are only ever read for a plugin that did **not** load —
    a switched-off or broken plugin has no cards to take the copy from, and
    importing it to ask would defeat the point of switching it off. For a plugin
    that did load, the cards carry all of it and the record stays empty.
    """

    name: str
    source: str
    path: Path | None = None
    label: str | None = None
    summary: str | None = None
    version: str | None = None
    homepage: str | None = None
    disabled: bool = False
    error: str | None = None
    contributions: list[object] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return not self.disabled and self.error is None

    @property
    def toggleable(self) -> bool:
        """Whether the page offers a switch.

        Nothing about DocMind's own integrations is optional, so they have none.
        Everything discovered is optional by construction — the plugin layer
        exists to keep non-essential capabilities out of the installer.
        """
        return self.source != SOURCE_BUILTIN

    @property
    def removable(self) -> bool:
        """Only a directory can be taken out from here.

        It is the one source the user put on disk themselves, and so the only
        one this application may move. Uninstalling a distribution would need
        the installer the bundled runtime does not ship.
        """
        return self.source == SOURCE_DIRECTORY

    def origin(self) -> PluginOrigin | None:
        """``None`` for a contribution installed with no plugin to attribute it to.

        That is the shape a test or an embedding host produces, and the page
        answers it the only honest way: no provenance line, and no plugin-level
        action that would have to guess what it was acting on.
        """
        if not self.name:
            return None
        return PluginOrigin(
            plugin=self.name,
            source=self.source,
            path=str(self.path) if self.path is not None else None,
            version=self.version,
            enabled=not self.disabled,
            active=self.active,
            error=self.error,
            toggleable=self.toggleable,
            removable=self.removable,
        )
