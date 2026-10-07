"""Reading a Pages document, by asking Pages to convert it.

``.pages`` is an IWA package: a zip of protobuf streams with snappy-compressed
blocks and no published schema. There is no viable pure-Python reader — the
obvious tools refuse it too (macOS's own ``textutil`` reports "the file isn't in
the correct format"). Reverse-engineering it is not a plugin-sized job.

So the conversion is delegated to the one program that certainly can do it.
Hand Pages a file, ask for Microsoft Word, and the result is something DocMind
already parses well. That makes this plugin macOS-only and dependent on Pages
being installed, which is a real cost — but the alternative is not importing
``.pages`` at all.

What that costs, measured on this machine (Pages 14.5, two real documents):

* **2.3 s** warm, **3.8 s** cold, i.e. the caller must not be the event loop;
* **no focus stealing** — the frontmost application stayed put through a cold
  start and a warm one, so driving Pages does not yank the user out of whatever
  they were doing. This was expected to be the worst problem here and is not
  one;
* **the exported docx has an empty ``docProps/core.xml``** — no title, no author;
* **structure survives only if the document has it** — both reference documents
  use nothing but Pages' 「正文」 paragraph style, so their export carries a single
  flat style and DocMind will report one section. That is the document, not the
  conversion; a document written with Pages' heading styles exports ``Heading N``
  and keeps its outline.

There is no title to be had, from anywhere. ``name of document`` reports the file
Pages was handed — which is our temporary copy, not the user's file — and the
package itself stores only UUIDs and a build version (``Metadata/Properties.plist``
has no title). The real filename was already lost when the document was staged.
So the title stays whatever the host called the document, exactly as it does for
every other format.

Nothing here imports DocMind. The boundary is deliberate: this module is about a
file format and a command-line tool, and keeping it that way is what lets the
plugin be tested without a host.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: Where macOS installs Pages. Checked rather than assumed: without it the export
#: fails with an opaque Apple Event error, and "install Pages" is a far better
#: thing to tell someone than that.
APPLICATION = Path("/Applications/Pages.app")

#: A backstop against a modal dialog, not a performance budget. A cold start is
#: ~4 s and a hundred-page document takes longer than that.
TIMEOUT_SECONDS = 120.0

#: Margin between AppleScript's own timer and the hard kill. The AppleScript one
#: fires first and produces a reportable error; the other only exists in case
#: ``osascript`` itself wedges. Note the block ends with ``end timeout``, not
#: ``end with timeout`` — the longer form does not parse, and the error it gives
#: ("预期是 timeout 等等，却找到 with") does not say so.
_GRACE_SECONDS = 15.0

#: The marker that makes a zip a Pages document rather than some other package.
#: ``Index/Document.iwa`` holds the body text.
_BODY_MEMBER_SUFFIX = ".iwa"

_EXPORT_SCRIPT = """\
on run argv
  set sourceFile to POSIX file (item 1 of argv)
  set targetFile to POSIX file (item 2 of argv)
  with timeout of {timeout} seconds
    tell application "Pages"
      set theDocument to open sourceFile
      export theDocument to targetFile as Microsoft Word
      close theDocument saving no
    end tell
  end timeout
end run
"""

#: Pages is a single GUI application with a single front window's worth of state.
#: Three exports at once — which is what a directory import produces, since the
#: batch runs three at a time — would open three documents in it and race their
#: dialogs. Serialising costs throughput nobody is waiting on and removes a class
#: of failure that would be very hard to diagnose from a report of "it hung".
_EXPORT_LOCK = threading.Lock()


class PagesError(Exception):
    """Base class for everything that can go wrong here."""


class PagesUnavailable(PagesError):
    """This machine cannot convert Pages documents at all."""


class PagesExportFailed(PagesError):
    """Pages ran and did not produce a usable document."""


class PagesTimeout(PagesError):
    """Pages was asked to export and never finished."""


@dataclass(frozen=True)
class Export:
    """A converted document."""

    docx: bytes


def unavailable_reason() -> str | None:
    """Why Pages documents cannot be imported here, or ``None`` when they can.

    Cheap and side-effect free: the import dialog asks for this every time it
    opens, and the answer decides whether ``.pages`` is offered at all.
    """
    if sys.platform != "darwin":
        return "Pages 文档只能在 macOS 上导入"
    if not APPLICATION.is_dir():
        return "未找到 Pages.app，请先安装 Pages"
    return None


def looks_like_pages(raw: bytes) -> str | None:
    """Whether these bytes are a Pages document. ``None`` means they are.

    A signature check alone would accept any zip — a `.docx` renamed, or an
    unrelated archive. A Pages package carries ``Index/Document.iwa`` and nothing
    else does, so reading the archive's index is the honest test. It is still
    cheap: the central directory is at the end of the file and nothing is
    decompressed.
    """
    if not raw.startswith(b"PK\x03\x04"):
        return "Pages 文件签名无效"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return "Pages 文件已损坏"
    if not any(name.endswith(_BODY_MEMBER_SUFFIX) for name in names):
        return "这不是 Pages 文档"
    return None


def export_docx(source: bytes, *, timeout: float = TIMEOUT_SECONDS) -> Export:
    """Convert one Pages document to docx by driving Pages.

    Blocking, and seconds long — a caller on an event loop must move it off.
    """
    executable = shutil.which("osascript")
    if unavailable_reason() is not None or executable is None:
        raise PagesUnavailable(unavailable_reason() or "系统缺少 osascript")

    # The document is written out because Pages only opens files, and read back
    # because it only exports files. Cleanup errors are ignored on purpose:
    # failing to remove a temporary directory must not replace the error the user
    # actually needs to see.
    with (
        _EXPORT_LOCK,
        tempfile.TemporaryDirectory(
            prefix="docmind-pages-", ignore_cleanup_errors=True
        ) as workspace,
    ):
        source_path = Path(workspace) / "source.pages"
        target_path = Path(workspace) / "export.docx"
        source_path.write_bytes(source)
        _run_export(executable, source_path, target_path, timeout)
        if not target_path.is_file():
            raise PagesExportFailed("Pages 没有生成导出文件")
        return Export(docx=target_path.read_bytes())


def _run_export(executable: str, source: Path, target: Path, timeout: float) -> None:
    script = _EXPORT_SCRIPT.format(timeout=int(timeout))
    try:
        completed = subprocess.run(
            [executable, "-e", script, str(source), str(target)],
            capture_output=True,
            text=True,
            # Later than AppleScript's own timer, so that timer wins and we get a
            # reported error rather than a killed process and an orphaned dialog.
            timeout=timeout + _GRACE_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise PagesTimeout(
            f"转换超时（{int(timeout)} 秒）：Pages 可能正等待一个对话框"
        ) from None
    if completed.returncode != 0:
        raise _failure(completed.stderr, timeout)


#: AppleScript's own timeout, raised when the ``with timeout`` block expires.
_APPLESCRIPT_TIMEOUT = "-1712"


def _failure(stderr: str, timeout: float) -> PagesError:
    detail = " ".join(stderr.split())
    if _APPLESCRIPT_TIMEOUT in detail:
        return PagesTimeout(f"Pages 在 {int(timeout)} 秒内没有完成转换")
    if "not find" in detail or "找不到" in detail:
        return PagesExportFailed(f"Pages 无法打开这个文档：{detail}")
    return PagesExportFailed(f"Pages 导出失败：{detail}" if detail else "Pages 导出失败")
