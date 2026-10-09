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

Every candidate becomes a :class:`PluginRecord` on the host, whether it loaded
or not. That is what lets the settings page offer a switch and a remove button:
a plugin the user turned off has no cards to be found through, so if it were not
recorded it could not be turned back on.

Design rules:

* **A broken plugin must not break startup.** Every load failure is collected
  and reported through ``PluginDiscovery`` instead of propagating. A missing
  integration is a degraded feature; refusing to boot is not an acceptable
  response to it.
* **Nothing is skipped silently.** A directory that declares a plugin but cannot
  be read is reported like any other failure. Skipping it quietly would make a
  plugin the user just cloned look identical to one they never cloned — which is
  the single thing this diagnostic channel exists to prevent.
* **A switched-off plugin is not imported.** Not merely absent from the page:
  its module never joins ``sys.modules`` and its directory never joins
  ``sys.path``. "Off" has to mean the code does not run, or the switch is a
  cosmetic one over a capability that still executes.
* **No vendor names anywhere.** Discovery is uniform, so a new plugin needs no
  change here, in the API layer, or in the renderer.
* **Discovery happens once per process.** Plugins are not hot-reloadable; adding
  or switching one requires a restart, same as any other dependency.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path

from app.plugins.contributions import PluginHost, install_contribution
from app.plugins.records import SOURCE_DIRECTORY, SOURCE_DISTRIBUTION, PluginRecord
from app.plugins.source_roots import (
    SourceRootDeclaration,
    SourceRootError,
    activate,
    iter_roots,
    read_declaration,
)
from app.plugins.state import PluginStateStore

logger = logging.getLogger(__name__)

#: The entry point group plugins declare under — in their installed metadata, or
#: in the ``pyproject.toml`` of a directory kept in the plugin directory.
PLUGIN_ENTRY_POINT_GROUP = "docmind.plugins"


class PluginLoadError(Exception):
    """Raised when an entry point returns something that cannot be installed."""


@dataclass
class PluginDiscovery:
    """What discovery found, and what happened to each thing it found.

    ``loaded`` and ``failed`` are the one-record-per-plugin diagnostics view,
    deliberately uniform in shape: ``error`` is present and empty for a plugin
    that loaded. A client that has to special-case a missing key is a client
    that will eventually treat the common case as malformed — and the common
    case in a working install is exactly the plugin that loaded.

    The records themselves live on the host, which is where the catalogue reads
    them: one list, so a plugin cannot be reported as loaded here and missing
    from the page.
    """

    loaded: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def as_dicts(self) -> list[dict[str, str]]:
        return [
            {
                "name": item.get("name", ""),
                "error": item.get("error", ""),
                "source": item.get("source", ""),
            }
            for item in (*self.loaded, *self.failed)
        ]


def load_plugins(
    host: PluginHost,
    plugins_dir: Path | None = None,
    *,
    state: PluginStateStore | None = None,
) -> PluginDiscovery:
    """Install every discoverable plugin onto ``host``.

    The host carries the registries a contribution installs into, so this
    function never needs to know which ones exist. ``plugins_dir`` is where
    directory-shaped plugins are looked for; ``None`` restricts discovery to
    installed distributions, which is what a caller with no data directory
    wants. ``state`` is the user's own switches; without it every plugin is
    treated as on, which is what a test that has no preference file wants.
    """
    discovery = PluginDiscovery()
    disabled = state.disabled() if state is not None else frozenset()
    for declaration, record in _directory_candidates(plugins_dir, host, discovery, disabled):
        _install(host, record, declaration.load, discovery)
    # A switched off name is off for *every* copy of that plugin, so the
    # distribution pass reads the decision off the directory pass rather than
    # making its own. Two rows for one switch would make one decision look like
    # two, and the copy left out of the list would still be loading the code the
    # user turned off.
    switched_off = {record.name for record in host.plugins if record.disabled}
    # A directory the user placed wins over an installed copy of the same
    # plugin: it is the more deliberate of the two, and it is the one a
    # developer iterating locally expects to be running. That is why the loop
    # below does not skip a name the directory pass already handled — the
    # conflict is reported, which is the only way the loser is discoverable.
    for candidate in _installed_candidates():
        name = candidate.name  # type: ignore[attr-defined]
        if name in disabled:
            if name in switched_off:
                # The same plugin, declared in two places. It is off once; two
                # rows would make one decision look like two.
                continue
            switched_off.add(name)
            host.plugins.append(_distribution_record(candidate, disabled=True))
            continue
        _install(host, _distribution_record(candidate), candidate.load, discovery)  # type: ignore[attr-defined]
    return discovery


def _install(
    host: PluginHost,
    record: PluginRecord,
    load: Callable[[], object],
    discovery: PluginDiscovery,
) -> None:
    try:
        contributions = _resolve(record.name, load)
    except Exception as error:  # noqa: BLE001 - one bad plugin must not stop the rest
        logger.warning("plugin %s failed to load: %s", record.name, error)
        _failed(host, record, _reason(error), discovery)
        return
    for contribution in contributions:
        try:
            install_contribution(host, contribution, plugin=record)
        except Exception as error:  # noqa: BLE001 - see above
            logger.warning("plugin %s failed to register: %s", record.name, error)
            _failed(host, record, _reason(error), discovery)
            return
    _attach(host, record)
    discovery.loaded.append({"name": record.name, "source": _source_text(record)})


def _failed(
    host: PluginHost, record: PluginRecord, error: str, discovery: PluginDiscovery
) -> None:
    record.error = error
    _attach(host, record)
    discovery.failed.append(
        {"name": record.name, "error": error, "source": _source_text(record)}
    )


def _attach(host: PluginHost, record: PluginRecord) -> None:
    """Put a record on the host once, whichever path reached it.

    ``install_contribution`` attaches a plugin as soon as its first contribution
    installs, so a plugin that fails on its second contribution is already
    there. Compared by identity rather than equality: two plugins may legitimately
    declare the same name — that is the conflict the directory pass reports — and
    an equality check would drop the second one.
    """
    if all(existing is not record for existing in host.plugins):
        host.plugins.append(record)


def _installed_candidates() -> list:
    try:
        found = entry_points(group=PLUGIN_ENTRY_POINT_GROUP)
    except Exception:
        logger.warning("plugin entry points unavailable", exc_info=True)
        return []
    return list(found)


def _distribution_record(candidate: object, *, disabled: bool = False) -> PluginRecord:
    """A record for a distribution-installed plugin.

    The declared metadata is read from the distribution when there is any, which
    is only ever used for a plugin that did not load — a plugin that loaded gets
    its copy from its own cards, and asking the distribution would be a second
    answer to a question already answered.
    """
    metadata = getattr(getattr(candidate, "dist", None), "metadata", None)
    version = _declared(metadata, "Version")
    return PluginRecord(
        name=candidate.name,  # type: ignore[attr-defined]
        source=SOURCE_DISTRIBUTION,
        label=_declared(metadata, "Name"),
        summary=_declared(metadata, "Summary"),
        version=version,
        disabled=disabled,
    )


def _declared(metadata: object, key: str) -> str | None:
    """One metadata field, or ``None``.

    Defensive on purpose: an entry point discovered in the wild may be a bare
    stub with no distribution behind it at all (that is how the loader's own
    tests build one), and ``None`` in is not a failure worth raising over.
    """
    if metadata is None:
        return None
    try:
        value = metadata[key]  # type: ignore[index]
    except (KeyError, TypeError):
        return None
    return value if isinstance(value, str) and value else None


def _directory_candidates(
    plugins_dir: Path | None,
    host: PluginHost,
    discovery: PluginDiscovery,
    disabled: frozenset[str],
) -> list[tuple[SourceRootDeclaration, PluginRecord]]:
    """Discover directory-shaped plugins, and make the enabled ones importable.

    Activation happens here, before anything is loaded, because a declaration's
    target module lives inside the directory being added. A plugin the user
    switched off is deliberately *not* activated: joining ``sys.path`` is the
    first half of running its code, so a switch that skipped only the import
    would leave the plugin's modules importable by anything else in the process.

    ``read_declaration`` itself stays a pure function of the filesystem, which is
    what lets the directory rules be tested without touching ``sys.path``.
    """
    if plugins_dir is None:
        return []
    candidates: list[tuple[SourceRootDeclaration, PluginRecord]] = []
    claimed: dict[str, Path] = {}
    for root in iter_roots(plugins_dir):
        try:
            declaration = read_declaration(root, PLUGIN_ENTRY_POINT_GROUP)
        except SourceRootError as error:
            logger.warning("plugin directory %s is unusable: %s", root, error)
            _failed(
                host,
                PluginRecord(name=root.name, source=SOURCE_DIRECTORY, path=root),
                str(error),
                discovery,
            )
            continue
        if declaration is None:
            continue
        if declaration.name in disabled:
            host.plugins.append(
                PluginRecord(
                    name=declaration.name,
                    source=SOURCE_DIRECTORY,
                    path=root,
                    label=declaration.distribution,
                    summary=declaration.description,
                    version=declaration.version,
                    disabled=True,
                )
            )
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
            _failed(
                host,
                PluginRecord(
                    name=declaration.name,
                    source=SOURCE_DIRECTORY,
                    path=root,
                    label=declaration.distribution,
                    version=declaration.version,
                ),
                f"模块 {declaration.module} 已由 {owner} 提供",
                discovery,
            )
            continue
        claimed[declaration.module] = root
        record = PluginRecord(
            name=declaration.name,
            source=SOURCE_DIRECTORY,
            path=root,
            version=declaration.version,
        )
        # Recorded where it is discovered, so the page lists plugins in the order
        # the directory holds them rather than in two passes — the ones that
        # failed first, then the ones that loaded.
        host.plugins.append(record)
        candidates.append((declaration, record))
    activate([declaration for declaration, _ in candidates])
    return candidates


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


def _source_text(record: PluginRecord) -> str:
    """Where a plugin came from, for the plugin page.

    An installed distribution has no interesting location — it is wherever it
    was installed to — so it reports nothing and the field stays empty. A
    directory is worth naming: it is the thing the user just cloned, and the
    thing they need to edit.
    """
    return str(record.path) if record.path is not None else ""


__all__ = [
    "PLUGIN_ENTRY_POINT_GROUP",
    "PluginDiscovery",
    "PluginLoadError",
    "load_plugins",
]
