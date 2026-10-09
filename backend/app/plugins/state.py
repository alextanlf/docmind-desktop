"""The plugins the user has switched off.

Kept in the data directory rather than in the plugin directory, so nothing the
loader scans can ever be mistaken for a plugin.

The switch is enforced at discovery: a plugin that is off is never imported, and
its directory never joins ``sys.path``. That is the whole difference between
"off" and "hidden" — a capability the user switched off must not run, and an
import is the moment it would.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: One file, next to the data that shares its lifetime. A plugin name is the
#: entry point name, so this is a set of strings and nothing else — there is no
#: per-plugin configuration to store here, and adding some would make this the
#: second place a plugin's settings live.
STATE_FILE = "plugin-state.json"


class PluginStateStore:
    """Which plugins are off, persisted across restarts.

    Enabling is the default and the absence of a name is how it is spelled, so
    the file only ever grows with plugins the user actively switched off, and
    removing a plugin from disk leaves nothing behind.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / STATE_FILE

    def disabled(self) -> frozenset[str]:
        """The names that are off. Read once per load, not once per plugin."""
        document = self._read()
        names = document.get("disabled")
        if not isinstance(names, list):
            return frozenset()
        return frozenset(name for name in names if isinstance(name, str) and name)

    def is_disabled(self, name: str) -> bool:
        return name in self.disabled()

    def set_enabled(self, name: str, *, enabled: bool) -> None:
        names = set(self.disabled())
        if enabled:
            names.discard(name)
        else:
            names.add(name)
        self._write(sorted(names))

    def forget(self, name: str) -> None:
        """Drop a plugin's name entirely — the state of something that is gone."""
        self.set_enabled(name, enabled=True)

    def _read(self) -> dict:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            # A state file we cannot read is not a reason to refuse to start.
            # Ignoring it means a plugin the user switched off comes back on,
            # which is visible, harmless and repeatable with one click — while
            # refusing to boot would take the whole application down over a
            # preference file. Logged rather than silent, because the user's
            # switch apparently did nothing and they deserve to know why.
            logger.warning("plugin state unreadable, treating every plugin as enabled: %s", error)
            return {}
        return document if isinstance(document, dict) else {}

    def _write(self, names: list[str]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps({"disabled": names}, ensure_ascii=False, indent=2)
        # Written aside and moved into place: a crash part way through a direct
        # write would leave a truncated file, which reads back as "every plugin
        # is enabled" — i.e. losing exactly the state this file exists to keep.
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.path)
