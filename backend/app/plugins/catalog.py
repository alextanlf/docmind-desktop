"""Derives the plugin catalogue from the installed contributions.

There is exactly one source of truth: the contributions ``load_plugins``
recorded on the host. A plugin card is not stored, configured or listed
anywhere — it is computed per request from the contribution's own declaration
and the live credential state. So a newly installed third-party plugin appears
in the settings page without a single line changing in this file or in the
renderer.

This class deliberately knows no kinds. It asks each contribution for its cards
and concatenates; the contributions own the differences between a credential
channel and a document format. Adding a kind therefore means adding a class and
never editing the catalogue — which is what "the plugin layer is a distribution
mechanism, not a remote-KB integration" has to mean in code.
"""
from __future__ import annotations

from app.plugins.contributions import PluginHost
from app.plugins.manifest import PluginManifest


class PluginCatalog:
    """Read model over the contributions installed on a ``PluginHost``."""

    def __init__(self, host: PluginHost) -> None:
        self.host = host

    def manifests(self) -> list[PluginManifest]:
        """One manifest per card, in install order.

        A contribution reports as many cards as it has things to configure — a
        remote provider contributes one per credential channel, a format
        contributes one. Concatenation is the whole algorithm.
        """
        manifests: list[PluginManifest] = []
        for contribution in self.host.contributions:
            manifests.extend(contribution.cards(self.host))
        return manifests

    def search(self, query: str) -> list[PluginManifest]:
        """Filter manifests by a free-text query.

        Matching happens here rather than in the renderer so the rule is
        testable and identical everywhere: every whitespace-separated term must
        match somewhere in the plugin's searchable text, case-insensitively via
        plain lowercasing. An empty query returns everything, which is what the
        page shows before the user types.
        """
        manifests = self.manifests()
        terms = [term for term in query.strip().lower().split() if term]
        if not terms:
            return manifests
        return [manifest for manifest in manifests if _matches(manifest, terms)]


def _matches(manifest: PluginManifest, terms: list[str]) -> bool:
    """Whether every term appears somewhere in the card's searchable text.

    The field list is duplicated in ``plugin-filter.ts`` and the two must stay
    in step: the renderer filters what it already has, the backend serves
    ``?q=``, and a card one of them finds while the other hides would be
    indistinguishable from a bug in the other.
    """
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
        )
        if part
    ).lower()
    return all(term in haystack for term in terms)
