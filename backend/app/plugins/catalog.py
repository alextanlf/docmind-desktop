"""Derives the plugin page's rows from the plugins installed on the host.

There is exactly one source of truth: the :class:`PluginRecord` list on the host.
A row is not stored, configured or listed anywhere — it is computed per request
from the plugin's own declaration, its own live credential state, and where
discovery found it. So a newly installed third-party plugin appears in the
settings page without a single line changing in this file or in the renderer.

Two shapes of row come out of one call, and both are :class:`PluginManifest` so
the renderer needs one code path:

* a plugin that loaded contributes **a card per contribution** — a remote
  provider contributes one per credential channel, a format contributes one;
* a plugin that is not contributing anything right now — switched off, failed to
  load, or declaring nothing — contributes **one row built from its own record**.

The second shape is the whole reason a switched-off plugin stays on the page: a
plugin that vanished when it was switched off would be indistinguishable from one
that was never installed, and could never be switched back on.

This class deliberately knows no kinds. It asks each contribution for its cards
and concatenates; the contributions own the differences between a credential
channel and a document format. Adding a kind therefore means adding a class and
never editing the catalogue — which is what "the plugin layer is a distribution
mechanism, not a remote-KB integration" has to mean in code.
"""
from __future__ import annotations

from dataclasses import replace

from app.plugins.contributions import KIND_PLUGIN, PluginHost
from app.plugins.manifest import PluginManifest
from app.plugins.records import PluginOrigin, PluginRecord


class PluginCatalog:
    """Read model over the plugins installed on a ``PluginHost``."""

    def __init__(self, host: PluginHost) -> None:
        self.host = host

    def manifests(self) -> list[PluginManifest]:
        """One row per thing the page shows, in install order.

        A contribution reports as many cards as it has things to configure —
        a remote provider contributes one per credential channel, a format
        contributes one. Concatenation is the whole algorithm.
        """
        rows: list[PluginManifest] = []
        for record in self.host.plugins:
            origin = record.origin()
            if not record.contributions:
                rows.append(_plugin_row(record, origin))
                continue
            for contribution in record.contributions:
                for card in contribution.cards(self.host):  # type: ignore[attr-defined]
                    # The plugin is attached here rather than by every
                    # contribution, so no contribution has to know it is part of
                    # one. A partial install keeps its cards *and* reports the
                    # failure through the same origin.
                    rows.append(replace(card, origin=origin))
        return rows

    def search(self, query: str) -> list[PluginManifest]:
        """Filter rows by a free-text query.

        Matching happens here rather than in the renderer so the rule is
        testable and identical everywhere: every whitespace-separated term must
        match somewhere in the row's searchable text, case-insensitively via
        plain lowercasing. An empty query returns everything, which is what the
        page shows before the user types.
        """
        manifests = self.manifests()
        terms = [term for term in query.strip().lower().split() if term]
        if not terms:
            return manifests
        return [manifest for manifest in manifests if _matches(manifest, terms)]


def _plugin_row(record: PluginRecord, origin: PluginOrigin | None) -> PluginManifest:
    """The row for a plugin that contributed no card.

    ``kind`` is :data:`KIND_PLUGIN` rather than any capability: this is not a
    thing to configure, it is a plugin to switch on, fix or remove. The copy is
    the plugin's own declared metadata — read from its manifest or its
    distribution metadata, never by importing it, since the plugin the user
    switched off is exactly the one whose code must not run.
    """
    return PluginManifest(
        id=f"{record.name}@{record.source}",
        kind=KIND_PLUGIN,
        provider=record.name,
        channel="",
        # Falling back to the name keeps the row identifiable even when nothing
        # declared anything — a directory whose manifest will not parse.
        label=record.label or record.name,
        summary=record.summary,
        version=record.version,
        homepage=record.homepage,
        origin=origin,
    )


def _matches(manifest: PluginManifest, terms: list[str]) -> bool:
    """Whether every term appears somewhere in the row's searchable text.

    The field list is duplicated in ``plugin-filter.ts`` and the two must stay
    in step: the renderer filters what it already has, the backend serves
    ``?q=``, and a card one of them finds while the other hides would be
    indistinguishable from a bug in the other.
    """
    origin = manifest.origin
    haystack = " ".join(
        part
        for part in (
            manifest.label,
            manifest.provider_label or "",
            manifest.summary or "",
            manifest.id,
            manifest.provider,
            manifest.channel,
            manifest.kind,
            manifest.tag or "",
            " ".join(manifest.extensions),
            " ".join(manifest.keywords),
            # The plugin's own name, which is what a user who cloned it knows it
            # as — 「docmind-tex」 appears in no label, only in the provenance.
            origin.plugin if origin is not None else "",
        )
        if part
    ).lower()
    return all(term in haystack for term in terms)
