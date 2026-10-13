"""拷贝型原件的名字、版本基线，以及启动时的一次对账。

副本从前落盘叫 ``raw.bin`` —— 不带扩展名。我们自己按内容读它（``/raw`` 的 Range、
``_original_info`` 的签名嗅探），所以一直没发现**别人是按名字认它的**：把这个路径交给
系统程序（"用系统程序打开原件"）时，macOS 只能看后缀，于是三种格式全被丢给
「归档实用工具」，用户看到的是"无法解压缩 raw.bin。格式不支持。"。实测
（``NSWorkspace.URLForApplicationToOpenURL``）::

    legacy-docx.bin -> /…/Archive Utility.app     legacy-docx.docx -> Microsoft Word
    legacy-pdf.bin  -> /…/Archive Utility.app     legacy-pdf.pdf  -> Adobe Acrobat
    legacy-md.bin   -> /…/Archive Utility.app     legacy-md.md    -> Xcode

所以副本必须带**它自己那个格式的**后缀。名字不能挑"打开得了的那个"：重新转换是按后缀
反解格式的（``load_stored_original`` → ``for_pickable_extension``），把一份 markdown
副本起名成 ``.txt`` 会让它当场报"不支持的来源"。

## 为什么是启动对账，而不是只在导入时改名

导入时改名只对**以后**的副本有效，而老机器上已经躺着一批 ``raw.bin``，它们正是用户点不
动的那些。判定依据（内容是哪种格式、记的路径还对不对）分别只在磁盘与数据库里，所以这里
与暂存清扫同一个形态 —— 拿数据库对账，而不是挂在某个流程的出口上。

对账顺手把"现在这一版"记成 ``original_revision`` 基线：拷贝型以前没有基线（那时它只可能
丢，不可能被改），于是用户在 Word 里改过副本之后没有任何东西能发现。有了基线，
``original_state`` 才算得出 ``changed``，界面才会给他「重新转换」这个出口 ——
"打开 → 改 → 回来重新转换"这一圈对拷贝型也成立。

**基线只补空，绝不覆盖。** 否则每次启动都会把用户改过的痕迹重新记成"原样"，
那条提示永远不会出现 —— 这条正是本模块最容易写错的地方。
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.document.builtin_formats import HTML_MEDIA_TYPE, MARKDOWN_MEDIA_TYPE
from app.document.formats import DocumentFormat, FormatRegistry
from app.storage.repositories import DocumentStore

logger = logging.getLogger(__name__)

#: 副本一律叫 ``raw.<后缀>``。固定的词干让"记录里的路径"与"磁盘上的文件"一眼对得上，
#: 也让重新导入时的清理只要认 `raw.*` 这一个形状。
_STORED_ORIGINAL_STEM = "raw"

#: 嗅探最多读这么多字节。够判签名、够看开头有没有 ``<html``；文本判定对 64 KiB 的
#: 截断也做了处理（见 `_looks_like_utf8_text`）。
_SNIFF_HEAD_BYTES = 64 * 1024

#: 有明显根标签才算网页。**不认**泛泛的 ``<``：markdown 里的行内 HTML 遍地都是，
#: 用它判会把 markdown 副本起名成 ``.html``，重新转换就会换一种解析器读它。
_HTML_MARKERS = (b"<html", b"<!doctype html", b"<body")


def stored_original_name(format: DocumentFormat) -> str:
    """副本在文档目录里的文件名（``raw.pdf``、``raw.docx``……）。

    取格式声明的**第一个**后缀（``.md`` 而不是 ``.markdown``）：反解时两个都能认出
    markdown，但第一个是导入与文件对话框一直在用的那个名字。
    """
    return f"{_STORED_ORIGINAL_STEM}{format.extensions[0]}"


def discard_stored_origins(directory: Path) -> None:
    """删掉这个文档目录里所有历史形态的副本（``raw.bin``、别的格式留下的 ``raw.pdf``……）。

    只认 "raw.<后缀>" 这一个形状：目录里还躺着 ``document.md`` 与 ``assets/``，
    按通配多删一个文件就是数据丢失。删的理由与从前一样 —— 记录说"没有原件"而磁盘上
    留着一份，磁盘占用与数据库就对不上。
    """
    for path in directory.glob(f"{_STORED_ORIGINAL_STEM}.*"):
        with suppress(OSError):
            if path.is_file():
                path.unlink()


def original_revision_of(path: Path) -> str | None:
    """一个路径的 ``mtime_ns:size``；拿不到就返回 ``None``。

    变更检测用 mtime 而不是内容哈希：为一次状态查询去重算 100 MB 的哈希是不合理的。
    它的失真方向是安全的 —— 被 ``touch`` 只是多提醒一次"要重新转换吗"，
    而真正的正确性由重新转换那一步重新读原件保证。
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return f"{info.st_mtime_ns}:{info.st_size}"


def sniff_original_format(path: Path, formats: FormatRegistry) -> DocumentFormat | None:
    """从字节判断这是一份什么格式的原件；认不出给 ``None``。

    **只给修历史名字用**，读取路径上没人靠它（``/raw`` 只看 PDF 签名，重新转换按后缀
    解析）。所以这里宁可认不出来、把名字原样留着，也不要猜：猜错的后缀会让重新转换
    用错解析器，把正文整篇换成另一种东西 —— 那比"这个名字还是打不开"糟得多。
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(_SNIFF_HEAD_BYTES)
    except OSError:
        return None
    # 声明的签名优先，长签名先试（以免短签名是个前缀而永远轮不到它）。
    for format in formats.binary_formats():
        magic = format.magic
        if magic is None or not head.startswith(magic):
            continue
        if format.validate is None or format.validate(head) is None:
            return format
    if _looks_like_html(head):
        return formats.for_media_type(HTML_MEDIA_TYPE)
    if _looks_like_utf8_text(head):
        return formats.for_media_type(MARKDOWN_MEDIA_TYPE)
    return None


def _looks_like_html(head: bytes) -> bool:
    lowered = head.lower()
    return any(marker in lowered for marker in _HTML_MARKERS)


def _looks_like_utf8_text(head: bytes) -> bool:
    """像不像一段 UTF-8 文本：没有 NUL，而且解得开。

    头 64 KiB 可能正好切在多字节字符中间，所以"只错在最后 3 个字节"的失败当作通过 ——
    要拒绝的是二进制（有 NUL，或读着读着就解不开），不是我们自己切的那一刀。
    """
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        return error.start >= len(head) - 3
    return True


@dataclass(frozen=True)
class OriginalCopyReport:
    """一次对账做了什么。数字分开记，是为了让日志与用例能区分"改对了"与"认不出"。"""

    #: 名字从 ``raw.bin`` 改成 ``raw.<后缀>`` 的份数。
    renamed: int = 0
    #: 这次补上版本基线的份数（含被改名的）。
    baselined: int = 0
    #: 内容认不出格式、名字原样留着的份数。它们仍然点不动，所以必须可见。
    unidentified: int = 0
    #: 记录里有、磁盘上已经没有的份数。
    missing: int = 0
    #: 想改名但改不动的份数（目标名已被占用，或 rename 失败）。
    failed: int = 0


def reconcile_original_copies(
    *, document_store: DocumentStore, formats: FormatRegistry
) -> OriginalCopyReport:
    """把拷贝型原件改成正确的名字，并给没有基线的补上基线。

    链接型（``source_path`` 有值）一概跳过：它们可能还留着一份没人读的旧副本，但那份
    字节已经不是"原件"了，改名与记基线都只会制造新的错觉。
    """
    renamed = baselined = unidentified = missing = failed = 0
    for document in document_store.list_all():
        if document.source_path is not None or not document.raw_path:
            continue
        path = Path(document.raw_path)
        if not path.is_file():
            missing += 1
            continue
        target = path
        if formats.for_extension(path.suffix) is None:
            format = sniff_original_format(path, formats)
            if format is None:
                unidentified += 1
                continue
            target = path.with_name(stored_original_name(format))
            if target != path:
                if target.exists() or not _rename(path, target):
                    failed += 1
                    continue
                renamed += 1
        # 只有还没记过基线时才写：覆盖它等于每次启动都把用户的改动抹成"原样"。
        revision = original_revision_of(target) if document.original_revision is None else None
        if target != path or revision is not None:
            document_store.update_stored_copy(
                document.id,
                raw_path=str(target) if target != path else None,
                original_revision=revision,
            )
        if revision is not None:
            baselined += 1

    report = OriginalCopyReport(
        renamed=renamed,
        baselined=baselined,
        unidentified=unidentified,
        missing=missing,
        failed=failed,
    )
    if report.renamed or report.baselined or report.missing:
        logger.info(
            "原件副本对账：改名 %d 份、补基线 %d 份（另有 %d 份记录在而文件不在）",
            report.renamed,
            report.baselined,
            report.missing,
        )
    if report.unidentified:
        # 认不出的那些仍然会被系统丢给归档实用工具。说清楚是"认不出"而不是"没处理"，
        # 免得下次有人看到"点了还是弹归档实用工具"时以为是这条对账没跑。
        logger.warning("原件副本对账：%d 份内容认不出格式，名字保持原样", report.unidentified)
    if report.failed:
        logger.warning("原件副本对账：%d 份改名失败（目标名被占用或磁盘出错）", report.failed)
    return report


def _rename(source: Path, target: Path) -> bool:
    """改名。**不用** ``os.replace``：目标已存在时应当什么都不做，而不是把人家盖掉。"""
    try:
        source.rename(target)
    except OSError:
        return False
    return True
