"""Third-party plugin discovery.

DocMind does not maintain a list of integrations. Built-in contributions are
installed explicitly in ``app.main``; anything else is discovered at startup.
There are two places a plugin can come from, and they exist for different
reasons rather than as two ways of doing one thing:

* **An installed distribution**, found through the ``docmind.plugins`` entry
  point group. This is the standard Python route and it costs nothing to keep
  working: a plugin installed into any directory on ``sys.path`` — for instance
  ``pip install --target <plugins dir>`` — is picked up here.
* **A source root**, found by scanning the plugin directory. This is what makes
  "develop it locally" and "clone it from GitHub" work at all: both produce a
  *directory*, and the packaged runtime ships no installer to turn one into a
  distribution.

Both kinds of candidate are handed to ``install_contribution`` identically, so
nothing downstream needs to know where a plugin came from — or, for that matter,
what kind of capability it contributes. Adding a kind means adding a class, not
editing this file.

Design rules:

* **A broken plugin must not break startup.** Every load failure is collected
  and reported through ``plugins_diagnostics()`` instead of propagating. A
  missing integration is a degraded feature; refusing to boot is not an
  acceptable response to it.
* **Nothing is skipped silently.** A directory that declares a plugin but cannot
  be read is reported like any other failure. Skipping it quietly would make a
  plugin the user just cloned look identical to one they never cloned — which is
  the single thing this diagnostic channel exists to prevent.
* **No vendor names anywhere.** Discovery is uniform, so a new plugin needs no
  change here, in the API layer, or in the renderer.
* **Discovery happens once per process.** Plugins are not hot-reloadable; adding
  one requires a restart, same as any other dependency.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path

from app.plugins.contributions import PluginHost, install_contribution
from app.plugins.source_roots import (
    SourceRootDeclaration,
    SourceRootError,
    activate,
    iter_roots,
    read_declaration,
)

logger = logging.getLogger(__name__)

#: The entry point group plugins declare under — in their installed metadata, or
#: in the ``pyproject.toml`` of a directory kept in the plugin directory.
PLUGIN_ENTRY_POINT_GROUP = "docmind.plugins"


class PluginLoadError(Exception):
    """Raised when an entry point returns something that cannot be installed."""


@dataclass
class PluginDiagnostics:
    """What happened while discovering third-party plugins."""

    loaded: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def as_dicts(self) -> list[dict[str, str]]:
        """One uniform record per plugin, whichever list it came from.

        ``error`` is present and empty for a plugin that loaded. A client that
        has to special-case a missing key is a client that will eventually treat
        the common case as malformed — and the common case in a working install
        is exactly the plugin that loaded.
        """
        return [
            {
                "name": item.get("name", ""),
                "error": item.get("error", ""),
                "source": item.get("source", ""),
            }
            for item in (*self.loaded, *self.failed)
        ]


def load_plugins(host: PluginHost, plugins_dir: Path | None = None) -> PluginDiagnostics:
    """Install every discoverable plugin onto ``host``.

    The host carries the registries a contribution installs into, so this
    function never needs to know which ones exist. ``plugins_dir`` is where
    directory-shaped plugins are looked for; ``None`` restricts discovery to
    installed distributions, which is what a caller with no data directory
    wants.
    """
    diagnostics = PluginDiagnostics()
    candidates: list[object] = _source_root_candidates(plugins_dir, diagnostics)
    # A directory the user placed wins over an installed copy of the same
    # plugin: it is the more deliberate of the two, and it is the one a
    # developer iterating locally expects to be running.
    candidates.extend(_installed_candidates())
    for candidate in candidates:
        name = candidate.name  # type: ignore[attr-defined]
        source = _source_of(candidate)
        try:
            contributions = _resolve(name, candidate.load)  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - one bad plugin must not stop the rest
            logger.warning("plugin %s failed to load: %s", name, error)
            diagnostics.failed.append({"name": name, "error": _reason(error), "source": source})
            continue
        for contribution in contributions:
            try:
                install_contribution(host, contribution)
            except Exception as error:  # noqa: BLE001 - see above
                logger.warning("plugin %s failed to register: %s", name, error)
                diagnostics.failed.append(
                    {"name": name, "error": _reason(error), "source": source}
                )
                break
        else:
            diagnostics.loaded.append({"name": name, "source": source})
    return diagnostics


def _installed_candidates() -> list:
    try:
        found = entry_points(group=PLUGIN_ENTRY_POINT_GROUP)
    except Exception:
        logger.warning("plugin entry points unavailable", exc_info=True)
        return []
    return list(found)


def _source_root_candidates(
    plugins_dir: Path | None, diagnostics: PluginDiagnostics
) -> list[SourceRootDeclaration]:
    """Discover directory-shaped plugins, and make them importable.

    Activation happens here, before anything is loaded, because a declaration's
    target module lives inside the directory being added. ``read_declaration``
    itself stays a pure function of the filesystem, which is what lets the
    directory rules be tested without touching ``sys.path``.
    """
    if plugins_dir is None:
        return []
    declarations: list[SourceRootDeclaration] = []
    claimed: dict[str, Path] = {}
    for root in iter_roots(plugins_dir):
        try:
            declaration = read_declaration(root, PLUGIN_ENTRY_POINT_GROUP)
        except SourceRootError as error:
            logger.warning("plugin directory %s is unusable: %s", root, error)
            diagnostics.failed.append({"name": root.name, "error": str(error), "source": str(root)})
            continue
        if declaration is None:
            continue
        owner = claimed.get(declaration.module)
        if owner is not None:
            # Whichever directory is mounted first would answer the import, so
            # the other plugin's code would never run while its card still
            # appeared to work. Two plugins cannot share a top-level module, and
            # saying so is cheaper than debugging the silence.
            logger.warning(
                "plugin directory %s re-declares module %s, already provided by %s",
                root,
                declaration.module,
                owner,
            )
            diagnostics.failed.append(
                {
                    "name": declaration.name,
                    "error": f"模块 {declaration.module} 已由 {owner} 提供",
                    "source": str(root),
                }
            )
            continue
        claimed[declaration.module] = root
        declarations.append(declaration)
    activate(declarations)
    return declarations


def _resolve(name: str, load: Callable[[], object]) -> list[object]:
    """Call a plugin's entry point and normalise its return shape.

    Accepts a single contribution, an iterable of them, or a callable factory
    returning either — the last form lets a plugin defer expensive setup until
    DocMind asks for it.

    Whether an item is a valid contribution is decided by
    ``install_contribution``, so this stays independent of what one must
    implement.
    """
    produced = load()
    if callable(produced) and not _looks_like_contribution(produced):
        produced = produced()
    if _looks_like_contribution(produced):
        return [produced]
    try:
        return list(produced)  # type: ignore[arg-type]
    except TypeError:
        raise PluginLoadError(
            f"entry point {name} returned {type(produced).__name__}, "
            "expected a plugin contribution"
        ) from None


def _looks_like_contribution(value: object) -> bool:
    return callable(getattr(value, "install", None)) and callable(
        getattr(value, "cards", None)
    )


def _reason(error: Exception) -> str:
    """A message worth showing, even for an exception that carries none.

    ``str(SomeError())`` is empty for anything raised without arguments, and an
    empty diagnostic is the one thing this channel cannot afford: the page would
    list the plugin that broke and then say nothing about why. A missing
    dependency does not need help here — the failure already names it, because
    ``import_target`` keeps the module name when it wraps the error.
    """
    return str(error) or type(error).__name__


def _source_of(candidate: object) -> str:
    """Where a candidate came from, for the plugin page.

    An installed distribution has no interesting location — it is wherever it
    was installed to — so it reports nothing and the field stays empty. A
    directory is worth naming: it is the thing the user just cloned, and the
    thing they need to edit.
    """
    root = getattr(candidate, "root", None)
    return str(root) if isinstance(root, Path) else ""
