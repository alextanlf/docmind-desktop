"""Shared setup for the plugin tests.

Everything here is about the fact that loading a plugin is a *real* import with
two global effects: the plugin's directory joins ``sys.path`` and its module
joins ``sys.modules``. Both outlive a single test otherwise, and the second is the
worse of the two — the next test would silently load an earlier test's plugin
instead of the one it wrote.
"""
from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

#: The repository root, so a test can reach the plugins that ship with it.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: Where the in-repo plugins live. They are the real artifacts, not fixtures:
#: loading one through the real loader is what proves "a directory is a plugin".
PLUGINS_SOURCE = REPO_ROOT / "plugins"


@pytest.fixture(autouse=True)
def plugin_imports() -> Iterator[None]:
    """Undo the two global effects of loading a plugin.

    Only modules that came from a directory this test added are dropped. Loading
    a plugin imports DocMind and its dependencies as a side effect, and evicting
    those breaks the rest of the session — C extensions such as numpy's cannot be
    imported twice in one process.
    """
    path = list(sys.path)
    modules = set(sys.modules)
    yield
    added = [entry for entry in sys.path if entry not in path]
    sys.path[:] = path
    for name in set(sys.modules) - modules:
        origin = getattr(sys.modules.get(name), "__file__", None)
        if origin and any(origin.startswith(entry) for entry in added):
            del sys.modules[name]
