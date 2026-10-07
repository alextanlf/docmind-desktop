"""The Pages plugin.

Most of what can go wrong here does not need Pages to be installed, and that is
the point: the machine running this suite may be Linux, or a Mac without Pages, so
the declarations and the error mapping are tested where they hold everywhere, and
the one thing that genuinely needs macOS is marked.

What is covered:

* **The declaration.** That this format says it cannot run somewhere, and that the
  file picker therefore does not offer it — the difference between a button that
  is absent and a button that always fails.
* **The payload check.** A `.pages` is a zip, exactly like a `.docx`, so the
  signature proves nothing and the archive's index has to be read.
* **The error mapping.** Three ways to fail, three codes, one of them retryable.
* **That the conversion is handed to DocMind's own Word parser**, which is what
  keeps this plugin from being a second implementation of reading `.docx`.
* **That the AppleScript compiles.** Not that it works — that needs Pages and a
  real document — but that it parses. This is not a hypothetical: the script
  originally closed its timeout block with ``end with timeout``, which is not
  AppleScript, and every import failed with a message about an unexpected ``with``
  at column 99. ``osacompile`` catches that in milliseconds and there is no other
  linter for it.
"""

from __future__ import annotations

import importlib
import io
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.document.formats import FormatRegistry
from app.schemas.imports import DownloadedDocument
from tests.document.test_docx import _docx, _paragraph, _styles
from tests.plugins.conftest import PLUGINS_SOURCE

PAGES_PLUGIN = PLUGINS_SOURCE / "docmind-pages"


@pytest.fixture
def driver():
    """The macOS-facing module, imported on its own (see the docstring there)."""
    sys.path.insert(0, str(PAGES_PLUGIN))
    return importlib.import_module("docmind_pages.pages")


@pytest.fixture
def plugin():
    """The contribution, as the loader would import it."""
    sys.path.insert(0, str(PAGES_PLUGIN))
    return importlib.import_module("docmind_pages")


def _package(members: list[str]) -> bytes:
    """A zip holding the given names — a stand-in for a real document."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in members:
            archive.writestr(name, "x")
    return buffer.getvalue()


def _document(plugin, raw: bytes = b"PK\x03\x04") -> DownloadedDocument:
    return DownloadedDocument(
        title="<uuid>.pages",
        source_url="staged://abc",
        media_type=plugin.plugin.format.media_type,
        raw_bytes=raw,
    )


def _raiser(error: Exception):
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise error

    return _raise


# -- where it can run --------------------------------------------------------


def test_it_declines_on_a_platform_without_pages(driver, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    assert driver.unavailable_reason() == "Pages 文档只能在 macOS 上导入"


def test_it_declines_when_pages_is_not_installed(driver, monkeypatch) -> None:
    monkeypatch.setattr(driver, "APPLICATION", Path("/Applications/Definitely-Not-Pages.app"))

    assert driver.unavailable_reason() == "未找到 Pages.app，请先安装 Pages"


def test_it_reports_nothing_when_it_can_run(driver, tmp_path, monkeypatch) -> None:
    installed = tmp_path / "Pages.app"
    installed.mkdir()
    monkeypatch.setattr(driver, "APPLICATION", installed)

    assert driver.unavailable_reason() is None


def test_the_format_carries_that_declaration(plugin) -> None:
    # Wired to the same callable, not a copy of its logic: the picker asks the
    # format, and the format has to be the thing that knows.
    assert plugin.plugin.format.availability is plugin.pages.unavailable_reason


def test_an_unavailable_format_is_not_offered(plugin) -> None:
    """What the declaration is for.

    Offering `.pages` on a machine that cannot convert one is a button that
    exists only to fail.
    """
    blocked = replace(plugin.plugin.format, availability=lambda: "未找到 Pages.app")
    registry = FormatRegistry()
    registry.register(blocked)

    assert registry.pickable_formats() == ()
    assert registry.pickable_extensions() == ()
    assert registry.for_pickable_extension(".pages") is None
    # The suffix still resolves. Availability decides what is *offered*, not what
    # the scanner may find inside a directory the user imported.
    assert registry.for_extension(".pages") is blocked


# -- what a Pages document looks like ----------------------------------------


def test_a_package_is_recognised_by_its_index_not_its_signature(driver) -> None:
    # A `.docx` is a zip too, so the magic number only proves "this is a package".
    word = _package(["word/document.xml", "[Content_Types].xml"])

    assert driver.looks_like_pages(word) == "这不是 Pages 文档"
    assert driver.looks_like_pages(_package(["Index/Document.iwa"])) is None
    assert driver.looks_like_pages(b"just some text") == "Pages 文件签名无效"


def test_a_truncated_package_is_reported_as_damaged(driver) -> None:
    broken = _package(["Index/Document.iwa"])[:40]

    # Has the signature, is not a readable archive.
    assert driver.looks_like_pages(broken) == "Pages 文件已损坏"


# -- failures -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (
            "unavailable",
            "SOURCE_UNSUPPORTED",
            False,
        ),
        ("timeout", "PARSE_TIMEOUT", True),
        ("failed", "PARSE_FAILED", False),
    ],
)
def test_each_failure_becomes_a_message_the_user_can_read(
    plugin, monkeypatch, error, code, retryable
) -> None:
    """The whole reason `service.py` preserves a parser's own DomainError.

    Without it every one of these would reach the user as 「文档解析失败」.
    """
    raised_error = {
        "unavailable": plugin.pages.PagesUnavailable("未找到 Pages.app，请先安装 Pages"),
        "timeout": plugin.pages.PagesTimeout("Pages 在 120 秒内没有完成转换"),
        "failed": plugin.pages.PagesExportFailed("Pages 导出失败：不能转换"),
    }[error]
    monkeypatch.setattr(plugin.pages, "export_docx", _raiser(raised_error))

    with pytest.raises(DomainError) as raised:
        plugin.parse_pages(_document(plugin))

    assert raised.value.code == code
    assert raised.value.message == str(raised_error)
    assert raised.value.retryable is retryable


def test_a_timeout_is_worth_retrying_and_a_refusal_is_not(plugin, monkeypatch) -> None:
    """A dialog left open is transient; a machine without Pages is not.

    Getting this backwards either hides a real fix behind a retry button or
    offers a retry that can never work.
    """
    monkeypatch.setattr(plugin.pages, "export_docx", _raiser(plugin.pages.PagesTimeout("超时")))
    with pytest.raises(DomainError) as timed_out:
        plugin.parse_pages(_document(plugin))
    assert timed_out.value.retryable is True

    monkeypatch.setattr(
        plugin.pages, "export_docx", _raiser(plugin.pages.PagesUnavailable("未找到 Pages.app"))
    )
    with pytest.raises(DomainError) as refused:
        plugin.parse_pages(_document(plugin))
    assert refused.value.retryable is False


# -- the conversion ----------------------------------------------------------


def test_the_export_is_read_by_the_word_parser(plugin, monkeypatch) -> None:
    """The conversion is not reimplemented here, and the test proves it ran.

    A stub returning a hand-built document would not produce a heading path, so
    the assertion below is evidence that DocMind's Word parser was actually used
    on the converted bytes.
    """
    converted = _docx(
        body=_paragraph("正文段落") + _paragraph("一节标题", style="Heading1"),
        styles=_styles([("Heading1", "heading 1")]),
    )
    monkeypatch.setattr(
        plugin.pages, "export_docx", lambda *a, **k: plugin.pages.Export(docx=converted)
    )

    parsed = plugin.parse_pages(_document(plugin))

    assert "正文段落" in parsed.markdown
    assert [section.heading_path for section in parsed.sections] == [[], ["一节标题"]]


def test_the_export_keeps_the_title_the_host_supplied(plugin, monkeypatch) -> None:
    """There is no title to be found, and none is invented.

    Pages' export carries an empty ``docProps/core.xml``, ``name of document``
    reports the temporary copy rather than the user's file, and the package stores
    only UUIDs — so the document keeps whatever the host called it, exactly like
    every other format. Fixing that belongs in staging, which loses the real
    filename, not here.
    """
    converted = _docx(body=_paragraph("正文"))
    monkeypatch.setattr(
        plugin.pages, "export_docx", lambda *a, **k: plugin.pages.Export(docx=converted)
    )

    parsed = plugin.parse_pages(_document(plugin))

    assert parsed.title == "<uuid>.pages"


def test_the_export_runs_in_a_directory_that_is_cleaned_up(driver, tmp_path, monkeypatch) -> None:
    """The document is written to disk and must not be left there."""
    created: list[Path] = []

    def _run(executable, source: Path, target: Path, timeout: float) -> None:
        created.append(source.parent)
        target.write_bytes(b"PK\x03\x04converted")

    monkeypatch.setattr(driver, "_run_export", _run)
    monkeypatch.setattr(driver.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(driver, "APPLICATION", tmp_path)  # present, so it proceeds

    export = driver.export_docx(b"PK\x03\x04 a document")

    assert export.docx == b"PK\x03\x04converted"
    assert created and not created[0].exists()


def test_the_working_directory_is_removed_even_when_the_export_fails(
    driver, tmp_path, monkeypatch
) -> None:
    created: list[Path] = []

    def _run(executable, source: Path, target: Path, timeout: float) -> None:
        created.append(source.parent)
        raise driver.PagesExportFailed("不能转换")

    monkeypatch.setattr(driver, "_run_export", _run)
    monkeypatch.setattr(driver.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(driver, "APPLICATION", tmp_path)

    with pytest.raises(driver.PagesExportFailed):
        driver.export_docx(b"PK\x03\x04 a document")

    assert created and not created[0].exists()


def test_the_export_refuses_before_spawning_when_it_cannot_run(driver, monkeypatch) -> None:
    """A clear refusal, not a failed subprocess with an opaque Apple Event error."""
    monkeypatch.setattr(driver, "APPLICATION", Path("/Applications/Definitely-Not-Pages.app"))

    with pytest.raises(driver.PagesUnavailable):
        driver.export_docx(b"PK\x03\x04 a document")


def test_a_reported_timeout_is_distinguished_from_another_failure(driver) -> None:
    # AppleScript reports its own timeout as -1712; treating it as a generic
    # failure would take away the retry that actually helps.
    timed_out = driver._failure("execution error: AppleEvent 已超时。 (-1712)", 60)

    assert isinstance(timed_out, driver.PagesTimeout)


def test_a_document_pages_cannot_open_says_so(driver) -> None:
    failure = driver._failure("execution error: 不能打开文稿。找不到 (-1728)", 60)

    assert isinstance(failure, driver.PagesExportFailed)
    assert "无法打开" in str(failure)


# -- the AppleScript ---------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="osacompile 只在 macOS 上存在")
def test_the_apple_script_compiles(driver, tmp_path) -> None:
    script = driver._EXPORT_SCRIPT.format(timeout=5)

    compiled = subprocess.run(
        ["osacompile", "-e", script, "-o", str(tmp_path / "export.scpt")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert compiled.returncode == 0, compiled.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="osacompile 只在 macOS 上存在")
def test_the_compiler_check_would_notice_a_broken_script(driver, tmp_path) -> None:
    """The check above only means something if it can fail.

    ``end with timeout`` is what this script used to say, and it is not
    AppleScript — every export died on it.
    """
    broken = driver._EXPORT_SCRIPT.format(timeout=5).replace("end timeout", "end with timeout")

    compiled = subprocess.run(
        ["osacompile", "-e", broken, "-o", str(tmp_path / "broken.scpt")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert compiled.returncode != 0
    assert "with" in compiled.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="osacompile 只在 macOS 上存在")
def test_the_script_takes_its_paths_as_arguments(driver) -> None:
    """Never interpolated into the script text.

    The staging directory can contain characters that would end the AppleScript
    string, and a path built by string formatting is one quoting rule away from
    being an injection.
    """
    script = driver._EXPORT_SCRIPT.format(timeout=5)

    assert "item 1 of argv" in script
    assert "item 2 of argv" in script
    assert str(driver.TIMEOUT_SECONDS) not in script


def test_the_format_wires_up_everything_the_plugin_declares(plugin) -> None:
    """The declarations are the plugin's whole interface to the host.

    An attribute left off a `DocumentFormat` does not fail loudly — the format
    still registers — it simply does less. A missing `validate` means a `.docx`
    renamed to `.pages` is accepted, and a missing `availability` means a button
    that always fails.
    """
    declared = plugin.plugin.format

    assert declared.parse is plugin.parse_pages
    assert declared.validate is plugin.pages.looks_like_pages
    assert declared.availability is plugin.pages.unavailable_reason
    assert declared.magic == b"PK\x03\x04"
    assert declared.binary_payload is True
    assert declared.extensions == (".pages",)


def test_the_export_never_waits_forever(driver) -> None:
    # Two bounds: AppleScript's own timer, which reports an error, and a later
    # hard kill in case osascript itself wedges. Neither may be absent.
    assert driver.TIMEOUT_SECONDS > 0
    assert driver._GRACE_SECONDS > 0


def test_the_hard_kill_waits_longer_than_the_reporting_timeout(driver, monkeypatch) -> None:
    """The order of the two bounds matters.

    AppleScript's timer fires first and produces an error that can be reported.
    If the process were killed first, the reason would be lost and the user would
    get a timeout with no account of why.
    """
    captured: dict[str, float] = {}

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(command, **options):
        captured["timeout"] = options["timeout"]
        return _Completed()

    monkeypatch.setattr(driver.subprocess, "run", _run)

    driver._run_export("/usr/bin/osascript", Path("/tmp/a"), Path("/tmp/b"), 30.0)

    assert captured["timeout"] > 30.0


def test_an_export_that_hands_back_nothing_is_a_failure(driver, tmp_path, monkeypatch) -> None:
    """osascript can exit zero and leave no file behind.

    Reading a path that is not there would surface as an unpacking error from
    somewhere else entirely.
    """
    monkeypatch.setattr(driver, "_run_export", lambda *args: None)
    monkeypatch.setattr(driver.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(driver, "APPLICATION", tmp_path)

    with pytest.raises(driver.PagesExportFailed) as failure:
        driver.export_docx(b"PK\x03\x04 a document")

    assert "没有生成导出文件" in str(failure.value)


def test_concurrent_exports_are_serialised(driver, tmp_path, monkeypatch) -> None:
    """Pages is one GUI application, and a directory import runs three at a time.

    Three documents open in it at once is how a scripted application ends up
    reporting the wrong document, or hanging behind a dialog nobody can see.
    """
    guard = threading.Lock()
    live = 0
    peak = 0

    def _run(executable, source: Path, target: Path, timeout: float) -> None:
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with guard:
            live -= 1
        target.write_bytes(b"PK\x03\x04 converted")

    monkeypatch.setattr(driver, "_run_export", _run)
    monkeypatch.setattr(driver.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(driver, "APPLICATION", tmp_path)

    workers = [
        threading.Thread(target=driver.export_docx, args=(b"PK\x03\x04 a document",))
        for _ in range(3)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert peak == 1
