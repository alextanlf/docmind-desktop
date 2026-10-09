"""The plugin catalogue, and the two things a user may do to a plugin.

Read-only apart from the switch and the remove button. The catalogue derives
every row from what is installed, so the response changes automatically when a
plugin is added, switched off or taken out, and the renderer holds no list of its
own.

Both writes are *decisions about a plugin*, never about a path. The request names
a plugin; the name is looked up among the plugins discovery actually found, and
the directory that follows from it comes from our own scan. That is what keeps
"remove a plugin" from being "move whatever path the client sent".
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.errors import DomainError
from app.plugins.catalog import PluginCatalog
from app.plugins.records import PluginRecord
from app.plugins.state import PluginStateStore
from app.plugins.uninstall import UninstallError, uninstall
from app.schemas.plugins import (
    PluginDirectoryView,
    PluginManifestView,
    PluginStateView,
    PluginToggleRequest,
)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _catalog(request: Request) -> PluginCatalog:
    catalog = getattr(request.app.state, "plugin_catalog", None)
    if catalog is None:
        raise DomainError(
            "PLUGIN_CATALOG_UNAVAILABLE",
            "插件清单不可用",
            503,
            True,
            "稍后重试",
        )
    return catalog  # type: ignore[no-any-return]


def _state(request: Request) -> PluginStateStore:
    state = getattr(request.app.state, "plugin_state", None)
    if state is None:
        raise DomainError(
            "PLUGIN_CATALOG_UNAVAILABLE",
            "插件状态不可用",
            503,
            True,
            "稍后重试",
        )
    return state  # type: ignore[no-any-return]


def _record(request: Request, plugin: str) -> PluginRecord:
    """The discovered plugin called ``plugin``, or 404.

    Resolved against discovery rather than used as given: this is the step that
    turns a name from the client into a fact this process established itself.
    """
    host = getattr(request.app.state, "plugin_host", None)
    records = getattr(host, "plugins", None) or []
    for record in records:
        if record.name == plugin:
            return record  # type: ignore[no-any-return]
    raise DomainError("PLUGIN_UNKNOWN", f"没有名为 {plugin} 的插件", 404, False)


@router.get("", response_model=list[PluginManifestView])
async def list_plugins(
    request: Request,
    q: str = Query(default="", max_length=200),
) -> list[PluginManifestView]:
    """Every row of the plugin page, optionally narrowed by a free-text query.

    ``q`` is matched against each plugin's own declared text (label, provider
    label, summary, id, keywords and the plugin's own name) with every
    whitespace-separated term required to match. An empty query returns
    everything, including the plugins that are switched off.
    """
    return [manifest.view() for manifest in _catalog(request).search(q)]


@router.get("/diagnostics", response_model=list[dict[str, str]])
async def plugin_diagnostics(request: Request) -> list[dict[str, str]]:
    """What happened while discovering third-party plugins.

    Surfaced rather than logged-and-forgotten: a plugin that silently failed to
    load looks identical to one that was never installed. Empty for a build with
    no third-party plugins.
    """
    discovery = getattr(request.app.state, "plugin_discovery", None)
    return discovery.as_dicts() if discovery is not None else []


@router.get("/directory", response_model=PluginDirectoryView)
async def plugin_directory(request: Request) -> PluginDirectoryView:
    """The directory a plugin is installed by putting it in.

    Installing a plugin is a filesystem action, so the one thing a user cannot
    guess — and the one thing the server alone knows — has to come from here.
    """
    return PluginDirectoryView(path=str(request.app.state.settings.plugins_dir))


@router.put("/{plugin}/enabled", response_model=PluginStateView)
async def set_plugin_enabled(
    request: Request, plugin: str, body: PluginToggleRequest
) -> PluginStateView:
    """Switch a plugin off, or back on.

    Takes effect at the next start: plugins are discovered once per process, and
    a plugin that is off is not imported at all, so there is nothing in this
    process to unload. The response says so instead of leaving the page to imply
    the change is already live.
    """
    record = _record(request, plugin)
    if not record.toggleable:
        # DocMind's own integrations are not optional, and the switch would be a
        # lie: they are installed by the application, not found on disk.
        raise DomainError(
            "PLUGIN_NOT_TOGGLEABLE",
            f"{record.name} 随 DocMind 提供，不能停用",
            400,
            False,
        )
    _state(request).set_enabled(record.name, enabled=body.enabled)
    return PluginStateView(plugin=record.name, enabled=body.enabled)


@router.delete("/{plugin}", response_model=PluginStateView)
async def remove_plugin(request: Request, plugin: str) -> PluginStateView:
    """Take a directory plugin out of the plugin directory.

    Moved, not deleted, and reported with where it went: these files are the
    user's own checkout, and the answer to "undo" has to be a real location.
    A plugin installed as a distribution cannot be removed from here — the
    packaged runtime ships no installer to remove it with — so it is refused
    with a reason rather than failing halfway.
    """
    record = _record(request, plugin)
    if not record.removable:
        raise DomainError(
            "PLUGIN_NOT_REMOVABLE",
            f"{record.name} 不是放在插件目录里的插件，DocMind 不能移除它",
            400,
            False,
        )
    try:
        destination = uninstall(
            record,
            plugins_dir=request.app.state.settings.plugins_dir,
            data_dir=request.app.state.settings.data_dir,
        )
    except UninstallError as error:
        raise DomainError("PLUGIN_REMOVAL_FAILED", str(error), 400, False) from None
    # The switch is dropped with the files: a plugin that is no longer installed
    # has no state, and leaving its name behind would make a later install of the
    # same name start out switched off.
    _state(request).forget(record.name)
    return PluginStateView(plugin=record.name, enabled=False, removed_to=str(destination))
