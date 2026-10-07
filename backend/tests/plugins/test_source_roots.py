"""Plugins kept as directories: a working copy, or a clone of somebody's repo.

The plugin layer exists to keep capabilities out of the installer, which is only
half a mechanism if the sole way to add one back is a distribution on PyPI:
a developer iterating on their own plugin and a user who ran ``git clone`` both
have a *directory*. What is locked here:

* a directory is a plugin **only** by declaring itself one. Dropping an
  unrelated tree into the plugin directory must not make its contents
  importable — the difference between a plugin directory and a second
  ``site-packages``;
* a directory that declares a plugin but cannot be read is **reported**, not
  skipped. Silently ignoring it makes "my clone is broken" look exactly like
  "I never cloned it", which is the one thing the diagnostics channel exists to
  distinguish;
* plugin paths are **appended**, so neither the standard library nor DocMind
  can be displaced by something a user dropped in a folder.

Every plugin here is a real import, so the module cache is undone per test: a
plugin written by one test must never be the module another test loads.
"""

from __future__ import annotations

import importlib
import re
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import AppSettings
from app.document.builtin_formats import builtin_registry
from app.document.formats import DocumentFormat
from app.plugins import DocumentFormatContribution
from app.plugins.catalog import PluginCatalog
from app.plugins.contributions import PluginHost

#: The group under test. Read from the loader rather than hardcoded so a rename
#: cannot leave this file asserting on a string nothing declares any more.
from app.plugins.loader import PLUGIN_ENTRY_POINT_GROUP as GROUP
from app.plugins.loader import load_plugins
from app.plugins.source_roots import (
    SOURCE_MANIFEST,
    SourceRootError,
    activate,
    import_target,
    iter_roots,
    read_declaration,
)
from app.remote.registry import ProviderRegistry
from tests.conftest import RUNTIME_TOKEN, build_app

_MODULE = "docmind_tex_fixture"


def _plugin_body(
    *,
    name: str = "tex",
    label: str = "TeX 文档",
    extension: str = ".tex",
    media_type: str = "text/x-tex",
) -> str:
    """An importable plugin, so "it loaded" means the whole path worked."""
    return f'''\
from app.document.formats import DocumentFormat
from app.plugins import DocumentFormatContribution
from app.schemas.imports import DownloadedDocument, ParsedDocument


def parse_source(document: DownloadedDocument) -> ParsedDocument:
    return ParsedDocument(
        title=document.title,
        markdown=document.raw_bytes.decode("utf-8"),
        source_url=document.source_url,
        sections=[],
    )


plugin = DocumentFormatContribution(
    format=DocumentFormat(
        name="{name}",
        label="{label}",
        media_type="{media_type}",
        extensions=("{extension}",),
        parse=parse_source,
    ),
    label="{label}",
    summary="把{label}导入 DocMind",
    tag="文档格式",
)
'''


def _write_plugin(
    root: Path,
    *,
    name: str = "docmind-tex",
    module: str = _MODULE,
    body: str | None = None,
    src_layout: bool = False,
    quoted_group: bool = True,
) -> Path:
    """Materialise the directory a ``git clone`` would have produced."""
    source = root / "src" if src_layout else root
    source.mkdir(parents=True, exist_ok=True)
    (source / f"{module}.py").write_text(
        _plugin_body() if body is None else body, encoding="utf-8"
    )
    header = (
        f'[project.entry-points."{GROUP}"]'
        if quoted_group
        else f"[project.entry-points.{GROUP}]"
    )
    (root / SOURCE_MANIFEST).write_text(
        "\n".join(
            [
                "[project]",
                f'name = "{name}"',
                'version = "0.1.0"',
                "",
                header,
                f'{name} = "{module}:plugin"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def _host(store) -> PluginHost:
    return PluginHost(
        providers=ProviderRegistry(store),
        formats=builtin_registry(),
        credentials=store,
    )


class _FakeDistribution:
    """A plugin that is installed rather than checked out."""

    def __init__(self, name: str, produced: object) -> None:
        self.name = name
        self._produced = produced

    def load(self) -> object:
        return self._produced


# -- what a directory has to say to be a plugin -----------------------------


def test_a_directory_without_a_manifest_declares_nothing(tmp_path: Path) -> None:
    root = tmp_path / "just-notes"
    root.mkdir()
    (root / "thoughts.py").write_text("x = 1", encoding="utf-8")

    assert read_declaration(root, GROUP) is None


def test_a_project_that_is_not_a_plugin_declares_nothing(tmp_path: Path) -> None:
    """Dirty input: a perfectly good Python project that is simply not a plugin.

    It must be neither loaded nor treated as broken — the plugin directory is an
    ordinary directory a user may keep anything in.
    """
    root = tmp_path / "unrelated"
    root.mkdir()
    (root / SOURCE_MANIFEST).write_text(
        """\
[project]
name = "unrelated"
version = "1.0"

[project.scripts]
unrelated = "unrelated:main"
""",
        encoding="utf-8",
    )

    assert read_declaration(root, GROUP) is None


def test_a_declaration_carries_the_plugin_s_own_identity(tmp_path: Path) -> None:
    root = _write_plugin(tmp_path / "docmind-tex-clone")

    declaration = read_declaration(root, GROUP)

    assert declaration is not None
    assert (declaration.name, declaration.target) == ("docmind-tex", f"{_MODULE}:plugin")
    # The directory is often a checkout slug, so the manifest's own name is what
    # diagnostics should report.
    assert (declaration.distribution, declaration.version) == ("docmind-tex", "0.1.0")
    assert declaration.root == root


def test_both_spellings_of_the_group_are_accepted(tmp_path: Path) -> None:
    quoted = _write_plugin(tmp_path / "quoted")
    dotted = _write_plugin(tmp_path / "dotted", quoted_group=False)

    # `[project.entry-points.docmind.plugins]` parses into nested tables. It is
    # not the correct spelling for a dotted group, which is exactly why someone
    # will write it — and reading it as "declares no plugin" would be a lie
    # about their file.
    assert read_declaration(quoted, GROUP) is not None
    assert read_declaration(dotted, GROUP) is not None
    assert read_declaration(quoted, GROUP).name == read_declaration(dotted, GROUP).name


def test_an_unparsable_manifest_is_an_error_not_an_omission(tmp_path: Path) -> None:
    root = tmp_path / "docmind-broken"
    root.mkdir()
    (root / SOURCE_MANIFEST).write_text("[project\nname = oops", encoding="utf-8")

    with pytest.raises(SourceRootError) as error:
        read_declaration(root, GROUP)

    assert SOURCE_MANIFEST in str(error.value)


def test_a_directory_declaring_two_plugins_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "docmind-two"
    root.mkdir()
    (root / SOURCE_MANIFEST).write_text(
        "\n".join(
            [
                "[project]",
                'name = "docmind-two"',
                'version = "0.1.0"',
                "",
                f'[project.entry-points."{GROUP}"]',
                'first = "a:plugin"',
                'second = "b:plugin"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    # One directory is one plugin; with two, install order would decide which of
    # them wins a suffix, which is a coin toss the user cannot see.
    with pytest.raises(SourceRootError) as error:
        read_declaration(root, GROUP)

    assert "只能声明一个" in str(error.value)


def test_a_target_that_is_not_module_colon_attribute_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "docmind-target"
    root.mkdir()
    (root / SOURCE_MANIFEST).write_text(
        "\n".join(
            [
                "[project]",
                'name = "docmind-target"',
                'version = "0.1.0"',
                "",
                f'[project.entry-points."{GROUP}"]',
                'target = "docmind_target"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourceRootError) as error:
        read_declaration(root, GROUP)

    assert "模块:属性" in str(error.value)


def test_the_src_layout_puts_its_package_root_on_the_path(tmp_path: Path) -> None:
    flat = _write_plugin(tmp_path / "flat")
    layered = _write_plugin(tmp_path / "layered", src_layout=True)

    assert read_declaration(flat, GROUP).path_entries == (flat,)
    # A src-layout project keeps its packages one level down, so the root alone
    # would not make the target importable.
    assert read_declaration(layered, GROUP).path_entries == (layered, layered / "src")


def test_iter_roots_skips_files_and_dot_directories(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    (plugins / "b-plugin").mkdir(parents=True)
    (plugins / "a-plugin").mkdir()
    (plugins / ".git").mkdir()
    (plugins / "README.md").write_text("x", encoding="utf-8")

    assert [root.name for root in iter_roots(plugins)] == ["a-plugin", "b-plugin"]
    # A missing directory is empty, not an error: the plugins directory is
    # created lazily and may not exist yet.
    assert iter_roots(tmp_path / "absent") == []


# -- importing a target -----------------------------------------------------


def test_a_missing_module_names_itself_in_the_error() -> None:
    with pytest.raises(SourceRootError) as error:
        import_target("docmind_absent_dependency:plugin")

    # The bundled runtime ships no installer, so a plugin with third-party
    # dependencies is the likely failure; the name is the only actionable part.
    assert "docmind_absent_dependency" in str(error.value)


def test_a_missing_attribute_is_reported_with_its_module(tmp_path: Path) -> None:
    root = _write_plugin(tmp_path / "plugin", body="plugin = None\n")
    activate([read_declaration(root, GROUP)])

    with pytest.raises(SourceRootError) as error:
        import_target(f"{_MODULE}:nonexistent")

    assert "nonexistent" in str(error.value)


def test_dotted_attributes_are_walked(tmp_path: Path) -> None:
    body = "class _Holder:\n    plugin = 'reached'\n\nholder = _Holder()\n"
    root = _write_plugin(tmp_path / "plugin", body=body)
    activate([read_declaration(root, GROUP)])

    assert import_target(f"{_MODULE}:holder.plugin") == "reached"


# -- the sys.path rules -----------------------------------------------------


def test_activation_appends_so_nothing_can_displace_stdlib_or_docmind(
    tmp_path: Path,
) -> None:
    """A plugin directory holding ``json.py`` or ``app/`` must not win.

    Prepending is the obvious implementation and the one that can stop the
    application from starting; appending is the direction that cannot.
    """
    root = tmp_path / "hostile"
    root.mkdir()
    (root / "json.py").write_text("raise RuntimeError('shadowed')", encoding="utf-8")
    before = list(sys.path)

    added = activate([_declaration_for(root)])

    assert sys.path[: len(before)] == before, "the plugin path was prepended"
    assert sys.path[-1] == str(root)
    assert added == [str(root)]
    assert importlib.import_module("json").__file__ != str(root / "json.py")


def test_activation_does_not_add_the_same_path_twice(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    root.mkdir()

    first = activate([_declaration_for(root)])
    second = activate([_declaration_for(root)])

    # A restart within one process — which is what a fixture is — must not grow
    # sys.path without bound.
    assert first == [str(root)]
    assert second == []
    assert sys.path.count(str(root)) == 1


def _declaration_for(root: Path):
    from app.plugins.source_roots import SourceRootDeclaration

    return SourceRootDeclaration(name="x", target="x:plugin", root=root)


# -- discovery through the loader -------------------------------------------


def test_a_checked_out_plugin_loads_from_the_plugin_directory(tmp_path: Path, store) -> None:
    plugins = tmp_path / "plugins"
    root = _write_plugin(plugins / "docmind-tex")
    host = _host(store)

    diagnostics = load_plugins(host, plugins)

    assert diagnostics.failed == []
    assert host.formats.for_extension(".tex").name == "tex"
    # Visible with no other wiring: the catalogue reads contributions, so
    # installing one is the whole of "make its card appear".
    assert [m.id for m in PluginCatalog(host).manifests()] == ["tex:core"]
    # Its own modules are importable too, which is what lets a real plugin be
    # more than one file.
    assert str(root) in sys.path
    assert diagnostics.loaded == [{"name": "docmind-tex", "source": str(root)}]


def test_a_directory_that_is_not_a_plugin_is_neither_loaded_nor_mounted(
    tmp_path: Path, store
) -> None:
    """Dirty input: an unrelated tree sitting beside a real plugin.

    Mounting every child directory would make the plugin directory a second
    ``site-packages``, where anything a user happens to keep becomes importable
    for the whole application.
    """
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "docmind-tex")
    stray = plugins / "notes"
    stray.mkdir(parents=True)
    (stray / "docmind_stray.py").write_text("VALUE = 'stray'", encoding="utf-8")
    host = _host(store)

    load_plugins(host, plugins)

    assert str(stray) not in sys.path
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("docmind_stray")


def test_a_broken_plugin_directory_is_reported_not_skipped(tmp_path: Path, store) -> None:
    """The distinction the diagnostics channel exists for.

    "My clone is broken" must not look like "I never cloned it".
    """
    plugins = tmp_path / "plugins"
    root = plugins / "docmind-broken"
    root.mkdir(parents=True)
    (root / SOURCE_MANIFEST).write_text("[project\nname = oops", encoding="utf-8")
    host = _host(store)

    diagnostics = load_plugins(host, plugins)

    assert [item["name"] for item in diagnostics.failed] == ["docmind-broken"]
    assert diagnostics.failed[0]["source"] == str(root)
    assert diagnostics.failed[0]["error"]


def test_a_plugin_with_a_missing_dependency_says_which_one(tmp_path: Path, store) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(
        plugins / "docmind-heavy",
        name="docmind-heavy",
        body="import docmind_absent_dependency\n\nplugin = None\n",
    )
    host = _host(store)

    diagnostics = load_plugins(host, plugins)

    # ModuleNotFoundError stringifies to nothing; without digging out its name
    # the user is told "加载失败" and has no idea what to install.
    assert len(diagnostics.failed) == 1
    assert "docmind_absent_dependency" in diagnostics.failed[0]["error"]


def test_a_plugin_that_fails_without_a_message_still_says_something(
    tmp_path: Path, store
) -> None:
    """An empty diagnostic is worse than a vague one.

    ``str(SomeError())`` is empty for anything raised without arguments, and the
    panel would then list the plugin that broke and nothing else.
    """
    plugins = tmp_path / "plugins"
    _write_plugin(
        plugins / "docmind-silent",
        name="docmind-silent",
        module="docmind_silent_fixture",
        body="raise RuntimeError()\n",
    )
    host = _host(store)

    diagnostics = load_plugins(host, plugins)

    assert [item["error"] for item in diagnostics.failed] == ["RuntimeError"]


def test_one_broken_plugin_does_not_stop_the_good_one(tmp_path: Path, store) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(
        plugins / "a-broken",
        name="a-broken",
        module="docmind_broken_fixture",
        body="raise RuntimeError('boom')\n",
    )
    _write_plugin(plugins / "b-good", name="b-good", module="docmind_good_fixture")
    host = _host(store)

    diagnostics = load_plugins(host, plugins)

    assert [item["name"] for item in diagnostics.failed] == ["a-broken"]
    assert [item["name"] for item in diagnostics.loaded] == ["b-good"]
    assert host.formats.for_extension(".tex").name == "tex"


def test_two_directories_claiming_one_module_do_not_both_install(
    tmp_path: Path, store
) -> None:
    """Two plugins cannot own the same top-level module.

    Whichever directory is mounted first answers the import, so the other
    plugin's code would never run while its card still looked installed. That is
    the exact shape of bug the diagnostics channel exists to make visible, so it
    is detected up front instead of showing up as a plugin that "loaded" but
    does nothing.
    """
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "a-first", name="a-first", module="docmind_shared_fixture")
    _write_plugin(
        plugins / "b-second",
        name="b-second",
        module="docmind_shared_fixture",
        body=_plugin_body(
            name="texb", label="TeX（第二份）", extension=".texb", media_type="text/x-tex-b"
        ),
    )
    host = _host(store)

    diagnostics = load_plugins(host, plugins)

    assert [item["name"] for item in diagnostics.failed] == ["b-second"]
    assert "docmind_shared_fixture" in diagnostics.failed[0]["error"]
    assert host.formats.for_extension(".tex") is not None
    assert host.formats.for_extension(".texb") is None


def test_a_directory_plugin_wins_over_an_installed_one(
    tmp_path: Path, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented tie-break, which exists so it is not decided by chance.

    A developer with a checkout *and* an installed copy gets the checkout when
    both declare the same name.
    """
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "docmind-tex")
    installed = DocumentFormatContribution(
        format=DocumentFormat(
            name="tex_installed",
            label="TeX（已安装）",
            media_type="text/x-tex-installed",
            extensions=(".tex",),
            parse=lambda document: document,  # type: ignore[arg-type,return-value]
        ),
        label="TeX（已安装）",
    )
    monkeypatch.setattr(
        "app.plugins.loader._installed_candidates",
        lambda: [_FakeDistribution("docmind-tex", installed)],
    )
    host = _host(store)

    diagnostics = load_plugins(host, plugins)

    assert host.formats.for_extension(".tex").name == "tex"
    assert [m.id for m in PluginCatalog(host).manifests()] == ["tex:core"]
    # The loser is reported rather than quietly dropped, and the conflict names
    # the format that won.
    assert [item["name"] for item in diagnostics.failed] == ["docmind-tex"]
    assert "tex" in diagnostics.failed[0]["error"]


def test_diagnostics_have_one_shape_whether_a_plugin_loaded_or_not(
    tmp_path: Path, store
) -> None:
    """A client filters on ``error``; a missing key would make the *common* case
    — the plugin that loaded — look malformed instead of the failure."""
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "a-broken", name="a-broken", module="docmind_broken_fixture",
                  body="raise RuntimeError('boom')\n")
    _write_plugin(plugins / "b-good", name="b-good", module="docmind_good_fixture")
    host = _host(store)

    records = load_plugins(host, plugins).as_dicts()

    assert [sorted(record) for record in records] == [["error", "name", "source"]] * 2
    assert [(record["name"], record["error"] == "") for record in records] == [
        ("b-good", True),
        ("a-broken", False),
    ]


def test_the_default_does_not_scan_a_real_plugin_directory(
    tmp_path: Path, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No argument means no directory, not "the current user's directory".

    Defaulting to ``~/.docmind/plugins`` inside the loader is the plausible
    refactor this catches: it would make every caller — including the whole test
    suite — depend on whatever is installed on the machine running it.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    home_plugins = Path.home() / ".docmind" / "plugins"
    _write_plugin(home_plugins / "docmind-tex")
    host = _host(store)

    diagnostics = load_plugins(host)

    assert diagnostics.loaded == []
    assert host.formats.for_extension(".tex") is None
    assert str(home_plugins / "docmind-tex") not in sys.path


# -- configuration ----------------------------------------------------------


def test_the_plugin_directory_sits_under_the_data_directory(tmp_path: Path) -> None:
    settings = AppSettings(
        session_token=SecretStr("token"), data_dir=tmp_path / "docmind-data", environment="test"
    )

    # Under data_dir because the packaged application's own tree is read-only:
    # this is the only directory that can be put on sys.path regardless of where
    # the app was installed.
    assert settings.plugins_dir == tmp_path / "docmind-data" / "plugins"
    assert settings.plugins_dir.is_dir()


# -- end to end -------------------------------------------------------------


def test_a_cloned_plugin_reaches_the_api_with_no_other_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole promise: clone into the plugin directory, restart, done.

    No packaging, no registry entry, and no client change — the card and the
    file picker both come from the backend.
    """
    data_dir = tmp_path / "docmind-data"
    root = _write_plugin(data_dir / "plugins" / "docmind-tex")
    monkeypatch.setenv("DOCMIND_SESSION_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setenv("DOCMIND_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DOCMIND_ENVIRONMENT", "test")
    settings = AppSettings(
        session_token=SecretStr(RUNTIME_TOKEN), data_dir=data_dir, environment="test"
    )
    headers = {"X-DocMind-Token": RUNTIME_TOKEN}

    with TestClient(build_app(settings, providers={})) as client:
        plugins = client.get("/api/plugins", headers=headers).json()
        formats = client.get("/api/imports/formats", headers=headers).json()
        diagnostics = client.get("/api/plugins/diagnostics", headers=headers).json()

    assert [plugin["id"] for plugin in plugins] == ["tex:core"]
    assert plugins[0]["extensions"] == [".tex"]
    assert [entry["name"] for entry in formats] == ["pdf", "markdown", "docx", "tex"]
    assert [item for item in diagnostics if item.get("error")] == []
    assert [item["source"] for item in diagnostics] == [str(root)]


# -- the guide is the contract ----------------------------------------------


def test_the_guide_s_plugin_declaration_is_usable_as_written() -> None:
    """The guide's snippet is what a user pastes.

    A wrong spelling there is copied verbatim into someone's repository and then
    reported as 「declares no plugin」, which reads like a DocMind bug rather than
    a documentation bug. So the snippet is parsed and checked against the rules
    the loader actually enforces.
    """
    guide = (
        Path(__file__).resolve().parents[3] / "docs" / "插件开发指南.md"
    ).read_text(encoding="utf-8")
    # Selected by "this is a pyproject example" rather than by containing the
    # group name: filtering on the group would quietly drop a snippet whose
    # group is misspelled, which is the one drift that matters.
    snippets = [
        block
        for block in re.findall(r"```toml\n(.*?)```", guide, re.DOTALL)
        if "[project]" in block
    ]

    assert snippets, "指南里找不到 pyproject.toml 示例"
    for snippet in snippets:
        document = tomllib.loads(snippet)
        project = document["project"]
        # The directory rules require both of these, so an example without them
        # is an example that fails on first run.
        assert project["name"], snippet
        assert project["version"], snippet
        entries = (project.get("entry-points") or {}).get(GROUP)
        assert entries, snippet
        assert len(entries) == 1, snippet
        name, target = next(iter(entries.items()))
        assert name and ":" in target, snippet
