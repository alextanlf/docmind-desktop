"""Plugins kept as directories: a working copy, or a clone of somebody's repo.

The plugin layer exists to keep non-essential capabilities out of the installer.
That is only half a mechanism if the only way to add one back is a distribution
on PyPI — a developer iterating on their own plugin, and a user who cloned a
repository, both have a *directory*, not a wheel. Neither should need a build
step, and the bundled runtime ships no installer to run one.

So a plugin may also be a **source root**: a directory that declares itself the
same way an installed distribution does.

.. code-block:: toml

    # pyproject.toml
    [project]
    name = "docmind-tex"
    version = "0.1.0"

    [project.entry-points."docmind.plugins"]
    docmind-tex = "docmind_tex:plugin"

``git clone`` into the plugin directory is then the entire install, and editing
a working copy in place is the entire dev loop.

Two rules make this safe rather than merely convenient:

* **Only a directory that declares a plugin goes on ``sys.path``.** Dropping an
  unrelated tarball into the plugin directory must not make its contents
  importable — otherwise a stray ``app/`` in it would shadow DocMind's own
  package and the application would not start.
* **Paths are appended, never prepended.** The standard library and DocMind
  itself always win a name collision, which is the direction that cannot break
  a running application.

Nothing here knows the entry point group: the caller passes it, so the group is
declared in exactly one place (``loader.PLUGIN_ENTRY_POINT_GROUP``).
"""
from __future__ import annotations

import importlib
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: The file a directory must have to be considered at all. Reusing
#: ``pyproject.toml`` rather than inventing a DocMind-specific manifest keeps
#: the plugin directory a normal Python project, which is what makes a cloned
#: repository usable without conversion.
SOURCE_MANIFEST = "pyproject.toml"

#: Conventional ``src/`` layout. A project that uses it puts its packages one
#: level down, so the root alone would not make them importable.
_SRC_LAYOUT = "src"


class SourceRootError(Exception):
    """A directory declares a plugin but the declaration cannot be used.

    Raised only for a directory that *is* a candidate — one whose manifest names
    the group. A directory that says nothing about plugins is not an error, it is
    simply not a plugin.
    """


@dataclass(frozen=True)
class SourceRootDeclaration:
    """One plugin declared by a directory on disk.

    Deliberately mirrors ``importlib.metadata.EntryPoint`` for the two members
    the loader uses (``name`` and ``load``), so both sources of plugins flow
    through one resolution path instead of two.
    """

    #: The entry point name, e.g. ``docmind-tex``.
    name: str
    #: ``"module:attribute"``, verbatim from the manifest.
    target: str
    #: The directory the user placed (or cloned).
    root: Path
    #: ``[project] name``, for diagnostics — the directory name is often a
    #: checkout slug rather than the plugin's own name.
    distribution: str | None = None
    version: str | None = None

    @property
    def module(self) -> str:
        """The top-level module this plugin expects to own."""
        return self.target.partition(":")[0]

    @property
    def path_entries(self) -> tuple[Path, ...]:
        """Directories to add so ``target``'s module becomes importable."""
        source = self.root / _SRC_LAYOUT
        return (self.root, source) if source.is_dir() else (self.root,)

    def load(self) -> object:
        return import_target(self.target)


def iter_roots(plugins_dir: Path) -> list[Path]:
    """Every child directory of ``plugins_dir``, in a stable order.

    Directories only: a stray file (``.DS_Store``, a README, an unzipped
    ``.whl``) is not a candidate. Dot-directories are skipped so a checkout's
    own metadata never gets scanned as a plugin.
    """
    if not plugins_dir.is_dir():
        return []
    return sorted(
        (child for child in plugins_dir.iterdir() if child.is_dir() and not child.name.startswith(".")),
        key=lambda child: child.name,
    )


def read_declaration(root: Path, group: str) -> SourceRootDeclaration | None:
    """The plugin ``root`` declares, or ``None`` if it declares none.

    ``None`` is the common case and not a failure: the plugin directory is an
    ordinary directory that the user may keep anything in.
    """
    manifest = root / SOURCE_MANIFEST
    if not manifest.is_file():
        return None
    try:
        document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SourceRootError(f"{SOURCE_MANIFEST} 无法解析：{error}") from None

    project = document.get("project")
    if not isinstance(project, dict):
        return None
    declared = project.get("entry-points")
    if not isinstance(declared, dict):
        return None
    entries = _group_in(declared, group)
    if entries is None:
        return None
    if not isinstance(entries, dict) or not entries:
        raise SourceRootError(f"{SOURCE_MANIFEST} 的 {group} 条目是空的")
    if len(entries) > 1:
        # One directory is one plugin. Allowing several would make install order
        # decide which of them wins a name, and the guide's answer for "I have
        # two capabilities" is one entry point returning two contributions.
        raise SourceRootError(
            f"{SOURCE_MANIFEST} 声明了 {len(entries)} 个 {group} 条目；"
            "一个插件目录只能声明一个（多种能力请让该条目返回多个贡献）"
        )
    name, target = next(iter(entries.items()))
    if not isinstance(target, str) or ":" not in target:
        raise SourceRootError(
            f"entry point {name} 的目标必须是 「模块:属性」，收到 {target!r}"
        )
    distribution = project.get("name")
    version = project.get("version")
    return SourceRootDeclaration(
        name=str(name),
        target=target,
        root=root,
        distribution=distribution if isinstance(distribution, str) else None,
        version=version if isinstance(version, str) else None,
    )


def activate(declarations: Iterable[SourceRootDeclaration]) -> list[str]:
    """Append every declaration's import roots to ``sys.path``.

    Returns what was added, so a caller (or a test) can see the side effect
    rather than infer it. Duplicates are skipped, which keeps a restart in the
    same process — the fixture case — from growing ``sys.path`` without bound.
    """
    added: list[str] = []
    for declaration in declarations:
        for entry in declaration.path_entries:
            text = str(entry)
            if text in sys.path or text in added:
                continue
            sys.path.append(text)
            added.append(text)
    return added


def import_target(target: str) -> object:
    """Import ``"module:attribute"`` and return the attribute.

    Dotted attributes are supported (``"pkg.sub:plugin.create"``). A missing
    module is the most likely failure in the wild — the bundled runtime has no
    installer, so a plugin with third-party dependencies has to be told about
    the missing name rather than dying with a bare traceback.
    """
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        raise SourceRootError(f"entry point 目标必须是 「模块:属性」，收到 {target!r}")
    try:
        value: object = importlib.import_module(module_name)
    except ImportError as error:
        raise SourceRootError(f"无法导入 {module_name}：{error}") from None
    for part in attribute.split("."):
        try:
            value = getattr(value, part)
        except AttributeError:
            raise SourceRootError(
                f"{module_name} 里没有 {attribute}"
            ) from None
    return value


def _group_in(declared: dict, group: str) -> object | None:
    """Look ``group`` up under ``[project.entry-points]``.

    Both spellings occur in the wild and TOML parses them differently::

        [project.entry-points."docmind.plugins"]   # one key, dots and all
        [project.entry-points.docmind.plugins]     # nested tables

    Only the first is strictly correct for a dotted group name, but a directory
    that used the second is a directory someone will drop in, and reporting it as
    "declares no plugin" would be a lie about their file.
    """
    if group in declared:
        return declared[group]
    cursor: object = declared
    for part in group.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
        if cursor is None:
            return None
    return cursor
