"""Switching a plugin off, and taking one out.

Two properties are locked here, and both are about *undo*:

* the switch is persisted. A plugin the user turned off must still be off after a
  restart, or the switch is a filter on one page render wearing a control's
  clothes;
* removal **moves** the plugin and reports where it went. Those files are the
  user's own checkout, so the answer to "put it back" has to be a real location
  rather than a reassuring sentence.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

import app.plugins.uninstall as removal
from app.plugins.records import SOURCE_DIRECTORY, SOURCE_DISTRIBUTION, PluginRecord
from app.plugins.state import STATE_FILE, PluginStateStore
from app.plugins.uninstall import GRAVEYARD, UninstallError, uninstall


def _checkout(plugins: Path, name: str = "docmind-tex") -> Path:
    """A plugin directory of the shape ``git clone`` produces."""
    root = plugins / name
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return root


def _record(path: Path, *, source: str = SOURCE_DIRECTORY) -> PluginRecord:
    return PluginRecord(name=path.name, source=source, path=path)


# -- the switch -------------------------------------------------------------


def test_a_plugin_switched_off_stays_off(tmp_path: Path) -> None:
    store = PluginStateStore(tmp_path)
    assert store.disabled() == frozenset()

    store.set_enabled("docmind-tex", enabled=False)

    # A second instance stands in for the next process: the switch is on disk,
    # not in the object that set it.
    assert PluginStateStore(tmp_path).disabled() == frozenset({"docmind-tex"})
    assert PluginStateStore(tmp_path).is_disabled("docmind-tex")
    assert PluginStateStore(tmp_path).is_disabled("docmind-pages") is False


def test_switching_one_back_on_leaves_the_rest_off(tmp_path: Path) -> None:
    store = PluginStateStore(tmp_path)
    store.set_enabled("a", enabled=False)
    store.set_enabled("b", enabled=False)

    store.set_enabled("a", enabled=True)

    # Dirty input: one name removed from a set of two. Rewriting the file from an
    # empty set would silently switch `b` back on, which is a capability the user
    # turned off coming back without being asked.
    assert store.disabled() == frozenset({"b"})


def test_enabling_what_is_already_on_changes_nothing(tmp_path: Path) -> None:
    store = PluginStateStore(tmp_path)

    store.set_enabled("docmind-tex", enabled=True)

    assert store.disabled() == frozenset()
    assert json.loads((tmp_path / STATE_FILE).read_text(encoding="utf-8"))["disabled"] == []


def test_forgetting_a_plugin_drops_its_name_entirely(tmp_path: Path) -> None:
    """What removal does to the switch.

    Keeping the name would make a later install of the same plugin start out
    switched off — a decision the user never made, applied to something they had
    not installed yet.
    """
    store = PluginStateStore(tmp_path)
    store.set_enabled("docmind-tex", enabled=False)

    store.forget("docmind-tex")

    assert store.disabled() == frozenset()


def test_an_unreadable_state_file_does_not_stop_the_app(tmp_path: Path) -> None:
    """Dirty input: a preference file that is not JSON.

    Ignoring it costs the user a switch they can set again — visible, harmless
    and repeatable. Refusing to start would take the whole application down over
    a preference file. Logged rather than silent, because their switch apparently
    did nothing and they deserve to know why.
    """
    (tmp_path / STATE_FILE).write_text("{not json at all", encoding="utf-8")
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = _Capture()
    logger = logging.getLogger("app.plugins.state")
    logger.addHandler(handler)
    try:
        # Canary first: an empty list below must mean "nothing was logged", not
        # "this handler was never going to receive anything" — the failure mode
        # that makes a logging assertion pass for the wrong reason.
        logger.warning("canary")
        assert PluginStateStore(tmp_path).disabled() == frozenset()
    finally:
        logger.removeHandler(handler)

    assert "canary" in captured
    assert any("unreadable" in message for message in captured)


def test_a_state_file_of_the_wrong_shape_is_treated_as_empty(tmp_path: Path) -> None:
    """Dirty input: valid JSON, wrong shape."""
    (tmp_path / STATE_FILE).write_text('{"disabled": "docmind-tex"}', encoding="utf-8")

    assert PluginStateStore(tmp_path).disabled() == frozenset()


# -- removal ----------------------------------------------------------------


def test_removing_a_plugin_moves_it_out_of_the_plugin_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins = tmp_path / "plugins"
    root = _checkout(plugins)
    graveyard = tmp_path / "graveyard"
    monkeypatch.setattr(removal, "_container", lambda data_dir: graveyard)

    destination = uninstall(_record(root), plugins_dir=plugins, data_dir=tmp_path / "data")

    assert not root.exists()
    assert destination == graveyard / "docmind-tex"
    # Moved, not deleted: it is a checkout the user made, so undo has to be
    # possible and has to have somewhere to point at.
    assert (destination / "pyproject.toml").is_file()


def test_removing_a_plugin_twice_does_not_overwrite_the_first_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dirty input: the same plugin name removed again.

    Moving onto the first removal would delete it inside a second operation the
    user believed was about something else.
    """
    plugins = tmp_path / "plugins"
    graveyard = tmp_path / "graveyard"
    monkeypatch.setattr(removal, "_container", lambda data_dir: graveyard)
    first = _checkout(plugins)
    uninstall(_record(first), plugins_dir=plugins, data_dir=tmp_path / "data")

    # The same name, installed again — which is what a user re-cloning a plugin
    # they removed earlier produces.
    reinstalled = _checkout(plugins)
    (reinstalled / "pyproject.toml").write_text(
        "[project]\nname = 'second'\n", encoding="utf-8"
    )
    second = uninstall(_record(reinstalled), plugins_dir=plugins, data_dir=tmp_path / "data")

    assert second != graveyard / "docmind-tex"
    assert "docmind-tex" in (graveyard / "docmind-tex" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "second" in (second / "pyproject.toml").read_text(encoding="utf-8")


def test_a_symlinked_plugin_is_removed_as_a_link_not_as_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dirty input: the documented install, which is a symlink to a working copy.

    Resolving the plugin would make this act on the repository instead of the
    link — removing a user's checkout, the one outcome this design exists to
    avoid. It is also why the containment check is on the plugin's *parent*:
    resolving the plugin itself would follow the link and refuse the install the
    guide recommends.
    """
    checkout = tmp_path / "code" / "docmind-tex"
    checkout.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    link = plugins / "docmind-tex"
    link.symlink_to(checkout)
    monkeypatch.setattr(removal, "_container", lambda data_dir: tmp_path / "graveyard")

    uninstall(_record(link), plugins_dir=plugins, data_dir=tmp_path / "data")

    assert not link.exists()
    assert checkout.is_dir()
    assert (checkout / "pyproject.toml").is_file()


def test_a_dangling_symlink_can_still_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link whose checkout is already gone is still something to clean up."""
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    link = plugins / "docmind-tex"
    link.symlink_to(tmp_path / "gone")
    graveyard = tmp_path / "graveyard"
    monkeypatch.setattr(removal, "_container", lambda data_dir: graveyard)

    uninstall(_record(link), plugins_dir=plugins, data_dir=tmp_path / "data")

    assert not link.exists()
    assert (graveyard / "docmind-tex").is_symlink()


def test_a_path_outside_the_plugin_directory_is_refused(tmp_path: Path) -> None:
    """The assertion that keeps this from being "move whatever path was named".

    The request carries a plugin *name*; the path comes from our own scan. This
    is what holds if that ever stops being true.
    """
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    elsewhere = tmp_path / "somewhere-else" / "docmind-tex"
    elsewhere.mkdir(parents=True)

    with pytest.raises(UninstallError):
        uninstall(_record(elsewhere), plugins_dir=plugins, data_dir=tmp_path / "data")

    assert elsewhere.is_dir()


def test_a_distribution_cannot_be_removed_from_here(tmp_path: Path) -> None:
    """There is no installer in the packaged runtime to remove one with."""
    record = PluginRecord(name="docmind-tex", source=SOURCE_DISTRIBUTION)

    with pytest.raises(UninstallError):
        uninstall(record, plugins_dir=tmp_path, data_dir=tmp_path)


def test_a_plugin_directory_that_vanished_is_reported(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()

    with pytest.raises(UninstallError):
        uninstall(_record(plugins / "docmind-tex"), plugins_dir=plugins, data_dir=tmp_path / "data")


def test_the_graveyard_is_used_where_there_is_no_platform_trash(tmp_path: Path) -> None:
    """The destination is a decision, so it is asserted rather than assumed.

    Handing files to the real trash is worth a branch — it is where a user
    already knows to look — but only where one exists. Everywhere else the
    application's own directory is the honest alternative, and it is the reason
    the response reports a path instead of claiming "moved to the trash".
    """
    container = removal._container(tmp_path)

    if sys.platform == "darwin" and (Path.home() / ".Trash").is_dir():
        assert container == Path.home() / ".Trash"
    else:
        assert container == tmp_path / GRAVEYARD
