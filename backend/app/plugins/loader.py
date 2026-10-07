"""Third-party plugin discovery via Python entry points.

DocMind does not maintain a list of integrations. Built-in contributions are
installed explicitly in ``app.main``; anything else is discovered at startup
from the ``docmind.plugins`` entry point group. A third party ships a plugin by
declaring it in their own ``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."docmind.plugins"]
    my-plugin = "my_package.docmind:plugin"

The referenced callable returns a contribution — or an iterable of them, since
one plugin may add several capabilities. DocMind hands each to the host, which
is all ``install`` needs: the loader never asks what kind a contribution is, so
a new kind needs no change here.

Design rules:

* **A broken plugin must not break startup.** Every load failure is collected
  and reported through ``plugins_diagnostics()`` instead of propagating. A
  missing integration is a degraded feature; refusing to boot is not an
  acceptable response to it.
* **No vendor names anywhere.** Discovery is uniform over entry points, so a
  new plugin needs no change here, in the API layer, or in the renderer.
* **Entry points are read once per process.** Plugins are not hot-reloadable;
  adding one requires a restart, same as any other dependency.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points

from app.plugins.contributions import PluginHost, install_contribution

logger = logging.getLogger(__name__)

#: The entry point group third-party plugins register under.
PLUGIN_ENTRY_POINT_GROUP = "docmind.plugins"


class PluginLoadError(Exception):
    """Raised when an entry point returns something that cannot be installed."""


@dataclass
class PluginDiagnostics:
    """What happened while discovering third-party plugins."""

    loaded: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def as_dicts(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.loaded] + [dict(item) for item in self.failed]


def load_plugins(host: PluginHost) -> PluginDiagnostics:
    """Install every discoverable plugin onto ``host``.

    The host carries the registries a contribution installs into, so this
    function never needs to know which ones exist.
    """
    diagnostics = PluginDiagnostics()
    for entry_point in _iter_entry_points():
        name = entry_point.name
        try:
            contributions = _resolve(entry_point)
        except Exception as error:  # noqa: BLE001 - one bad plugin must not stop the rest
            logger.warning("plugin %s failed to load: %s", name, error)
            diagnostics.failed.append({"name": name, "error": str(error) or type(error).__name__})
            continue
        for contribution in contributions:
            try:
                install_contribution(host, contribution)
            except Exception as error:  # noqa: BLE001 - see above
                logger.warning("plugin %s failed to register: %s", name, error)
                diagnostics.failed.append(
                    {"name": name, "error": str(error) or type(error).__name__}
                )
                break
        else:
            diagnostics.loaded.append({"name": name})
    return diagnostics


def _iter_entry_points() -> Iterable:
    try:
        found = entry_points(group=PLUGIN_ENTRY_POINT_GROUP)
    except Exception:
        logger.warning("plugin entry points unavailable", exc_info=True)
        return ()
    return tuple(found)


def _resolve(entry_point) -> list[object]:
    """Call the entry point and normalise its return shape.

    Accepts a single contribution, an iterable of them, or a callable factory
    returning either — the last form lets a plugin defer expensive setup until
    DocMind asks for it.

    Whether an item is a valid contribution is decided by
    ``install_contribution``, so this stays independent of what one must
    implement.
    """
    produced = entry_point.load()
    if callable(produced) and not _looks_like_contribution(produced):
        produced = produced()
    if _looks_like_contribution(produced):
        return [produced]
    try:
        return list(produced)
    except TypeError:
        raise PluginLoadError(
            f"entry point {entry_point.name} returned {type(produced).__name__}, "
            "expected a plugin contribution"
        ) from None


def _looks_like_contribution(value: object) -> bool:
    return callable(getattr(value, "install", None)) and callable(
        getattr(value, "cards", None)
    )
