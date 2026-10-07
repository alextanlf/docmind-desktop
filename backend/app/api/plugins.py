"""The plugin catalogue endpoint.

Read-only and vendor-agnostic: it derives every card from the provider
registry, so the response changes automatically when a plugin is added or
removed. The renderer holds no list of its own.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.errors import DomainError
from app.plugins.catalog import PluginCatalog
from app.schemas.plugins import PluginManifestView

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


@router.get("", response_model=list[PluginManifestView])
async def list_plugins(
    request: Request,
    q: str = Query(default="", max_length=200),
) -> list[PluginManifestView]:
    """Every plugin card, optionally narrowed by a free-text query.

    ``q`` is matched against each plugin's own declared text (label, provider
    label, summary, id and keywords) with every whitespace-separated term
    required to match. An empty query returns the full catalogue.
    """
    return [manifest.view() for manifest in _catalog(request).search(q)]


@router.get("/diagnostics", response_model=list[dict[str, str]])
async def plugin_diagnostics(request: Request) -> list[dict[str, str]]:
    """What happened while discovering third-party plugins.

    Surfaced rather than logged-and-forgotten: a plugin that silently failed to
    load looks identical to one that was never installed. Empty for a build with
    no third-party plugins.
    """
    diagnostics = getattr(request.app.state, "plugin_diagnostics", None)
    return diagnostics.as_dicts() if diagnostics is not None else []