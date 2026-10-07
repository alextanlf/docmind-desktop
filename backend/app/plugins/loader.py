"""Third-party plugin discovery via Python entry points.

DocMind does not maintain a list of integrations. Built-in providers are
registered explicitly in ``app.main``; anything else is discovered at startup
from the ``docmind.plugins`` entry point group. A third party ships a plugin by
declaring it in their own ``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."docmind.plugins"]
    my-integration = "my_package.docmind:plugin"

The referenced callable returns a ``PluginContribution`` (or an iterable of
them). DocMind then:

* registers the provider and its credential spec on the shared registry, so it
  flows through the exact same credential / discovery / import paths as a
  built-in one — there is no second code path for third-party plugins;
* registers any notification targets it declares;
* derives its plugin card from the manifest, with no UI change.

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

from app.plugins.manifest import PluginContribution

logger = logging.getLogger(__name__)

#: The entry point group third-party plugins register under.
PLUGIN_ENTRY_POINT_GROUP = "docmind.plugins"


class PluginLoadError(Exception):
    """Raised by :func:`load_plugins` callers that want a hard failure.

    Only tests and the diagnostic endpoint use it; startup uses the collected
    form below.
    """


@dataclass
class PluginDiagnostics:
    """What happened while discovering third-party plugins."""

    loaded: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def as_dicts(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.loaded] + [dict(item) for item in self.failed]


def load_plugins(registry, notification_hub=None) -> PluginDiagnostics:
    """Register every discoverable plugin onto ``registry``.

    ``registry`` is a ``ProviderRegistry``; ``notification_hub`` an optional
    ``NotificationHub``. Both are duck-typed on purpose so this module stays
    importable without pulling in the remote stack (which matters for tests
    that only care about discovery semantics).
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
                _register(registry, notification_hub, contribution)
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
    except Exception:  # noqa: BLE001 - a broken environment must not block boot
        logger.warning("plugin entry points unavailable", exc_info=True)
        return ()
    return tuple(found)


def _resolve(entry_point) -> list[PluginContribution]:
    """Call the entry point and normalise its return shape.

    Accepts a single contribution, an iterable of them, or a callable factory
    returning either — the last form lets a plugin defer expensive setup until
    DocMind asks for it.
    """
    produced = entry_point.load()
    if callable(produced) and not isinstance(produced, PluginContribution):
        produced = produced()
    if isinstance(produced, PluginContribution):
        return [produced]
    items = list(produced)
    for item in items:
        if not isinstance(item, PluginContribution):
            raise PluginLoadError(
                f"entry point {entry_point.name} returned {type(item).__name__}, "
                "expected PluginContribution"
            )
    return items


def _register(registry, notification_hub, contribution: PluginContribution) -> None:
    registry.register(
        contribution.provider,
        contribution.is_configured,
        credential_spec=contribution.credential_spec,
    )
    if notification_hub is None:
        return
    for target in contribution.notifications or ():
        notification_hub.register(target)