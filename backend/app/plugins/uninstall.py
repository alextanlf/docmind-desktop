"""Taking a plugin out of the plugin directory.

Only a plugin that *is* a directory can be removed here, and only by moving it
aside. Those files are the user's — a plugin manager that deletes a checkout it
did not create is one nobody should let near their repository. So the plugin is
moved somewhere recoverable and the destination is reported back, rather than
this function describing itself as "delete".

Two rules keep it from becoming a general-purpose file mover:

* **The path is never taken from the request.** The API layer resolves a plugin
  *name* against the plugins discovery actually found, and the path comes from
  that record. The assertion below is what keeps a future caller from quietly
  turning this into "move whatever path the client sent".
* **A directory plugin is a direct child of the plugin directory, and stays
  one.** A symlink to a checkout is resolved by the caller's scan, not here, so
  the check is on the parent — resolving the plugin itself would follow the link
  and reject the documented symlink install.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from app.plugins.records import SOURCE_DIRECTORY, PluginRecord

#: Where a removed plugin goes when the platform has no trash to hand it to.
#: Inside the data directory: the application owns it, it is cleaned up with the
#: rest of the data, and it is somewhere the user can still reach the plugin.
GRAVEYARD = "removed-plugins"


class UninstallError(Exception):
    """A plugin cannot be removed, with a reason worth showing the user."""


def uninstall(record: PluginRecord, *, plugins_dir: Path, data_dir: Path) -> Path:
    """Move ``record``'s directory out of ``plugins_dir``, returning where it went."""
    if record.source != SOURCE_DIRECTORY or record.path is None:
        raise UninstallError("这个插件不是放在插件目录里的，DocMind 不能把它移走")
    root = record.path
    if root.parent.resolve() != plugins_dir.resolve():
        raise UninstallError("插件不在插件目录里，拒绝移除")
    if not os.path.lexists(root):
        # A dangling symlink still counts as something to remove; the directory
        # being gone entirely does not.
        raise UninstallError("插件目录已经不存在了")
    container = _container(data_dir)
    container.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = _unused(container / root.name)
    shutil.move(str(root), str(destination))
    return destination


def _container(data_dir: Path) -> Path:
    """The platform's own trash where there is one, ours where there is not.

    Handing files to the real trash is worth the branch: it is the place a user
    already knows how to look, and on macOS it is the one Finder offers "Put
    Back" from. Where the platform has no such directory, moving the plugin into
    a directory this application owns is the honest alternative — recoverable,
    and reported by path so the user is told where rather than told a story
    about a trash they do not have.
    """
    if sys.platform == "darwin":
        trash = Path.home() / ".Trash"
        if trash.is_dir():
            return trash
    return data_dir / GRAVEYARD


def _unused(path: Path) -> Path:
    """``path``, or a timestamped sibling when something already has that name.

    Moving onto an existing entry would replace a plugin the user removed
    earlier, which is a silent second deletion hidden inside the first.
    """
    if not os.path.lexists(path):
        return path
    return path.with_name(f"{path.name}-{int(time.time())}")
