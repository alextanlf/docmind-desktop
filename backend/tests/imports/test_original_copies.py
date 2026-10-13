"""拷贝型原件的名字与基线：对账那一步。

这些用例守的是两件容易被静默弄坏的事：**系统是按后缀选应用的**（`raw.bin` 会被丢给
归档实用工具 —— 这就是用户报的那个弹窗），以及**基线只能补空、不能覆盖**（覆盖了，
用户在 Word 里改过副本这件事就再也发现不了）。第二件尤其容易写错：对账每次启动都跑，
而"顺手把当前状态记成基线"看起来完全合理。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.document.builtin_formats import builtin_registry
from app.imports.original_copies import (
    original_revision_of,
    reconcile_original_copies,
    sniff_original_format,
    stored_original_name,
)
from app.storage.database import Database
from app.storage.models import DocumentRecord
from app.storage.repositories import DocumentStore, RepositoryStore

PDF_BYTES = b"%PDF-1.7\nlegacy body"
DOCX_BYTES = b"PK\x03\x04fake-docx-payload"
MARKDOWN_BYTES = "# 标题\n\n正文".encode()
HTML_BYTES = b"<!doctype html><html><body>hi</body></html>"
BINARY_BYTES = b"\x00\x01\x02not-a-document"


def _seed_copy(
    database: Database,
    tmp_path: Path,
    payload: bytes,
    *,
    name: str = "raw.bin",
    document_id: str = "doc-copy",
    source_path: str | None = None,
    original_revision: str | None = None,
) -> tuple[DocumentRecord, Path]:
    """建一篇拷贝型文档：字节在我们自己的目录里，记录里只有 `raw_path`。"""
    directory = tmp_path / "documents" / document_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    copy_path = directory / name
    copy_path.write_bytes(payload)
    (directory / "document.md").write_text("# 标题\n\n正文", encoding="utf-8")
    repository = RepositoryStore(database).create_local(name="拷贝型")
    document = DocumentStore(database).create(
        DocumentRecord(
            id=document_id,
            repository_id=repository.id,
            title="拷贝的文档",
            raw_path=str(copy_path),
            markdown_path=str(directory / "document.md"),
            source_type="import",
            source_path=source_path,
            original_revision=original_revision,
        )
    )
    return document, copy_path


def _reconcile(database: Database):
    return reconcile_original_copies(
        document_store=DocumentStore(database), formats=builtin_registry()
    )


def test_a_legacy_copy_gets_its_real_extension(database: Database, tmp_path: Path) -> None:
    """`raw.bin` → `raw.pdf`：后缀就是系统用来选程序的那一条信息。

    用户点"用系统程序打开原件"时，macOS 拿到的东西没有后缀，于是三个文档全被丢给
    归档实用工具，弹"无法解压缩 raw.bin。格式不支持。"。名字必须跟着内容走。
    """
    document, copy_path = _seed_copy(database, tmp_path, PDF_BYTES)

    report = _reconcile(database)

    assert report.renamed == 1
    assert not copy_path.exists()
    renamed = copy_path.parent / "raw.pdf"
    assert renamed.read_bytes() == PDF_BYTES
    stored = DocumentStore(database).get(document.id)
    assert stored is not None
    assert stored.raw_path == str(renamed)


def test_the_renamed_copy_gets_a_baseline(database: Database, tmp_path: Path) -> None:
    """副本也要基线：没有它，用户在 Word 里改过之后没有任何东西能发现。"""
    document, copy_path = _seed_copy(database, tmp_path, PDF_BYTES)

    report = _reconcile(database)

    renamed = copy_path.parent / "raw.pdf"
    stored = DocumentStore(database).get(document.id)
    assert stored is not None
    assert stored.original_revision == original_revision_of(renamed)
    assert report.baselined == 1


def test_a_baseline_that_already_exists_is_never_overwritten(
    database: Database, tmp_path: Path
) -> None:
    """**对账最容易写错的一处。**

    用户已经改过副本（记录里的基线不是当前这一版）时，改名只能改名字：顺手把当前状态
    记成新基线，等于把"他改过"这件事抹掉，`original_state` 永远是 available，
    那条"原件已变过 → 重新转换"的提示就再也不会出现。
    """
    document, copy_path = _seed_copy(
        database, tmp_path, PDF_BYTES, original_revision="1:1"
    )

    report = _reconcile(database)

    assert (report.renamed, report.baselined) == (1, 0)
    stored = DocumentStore(database).get(document.id)
    assert stored is not None
    assert stored.original_revision == "1:1"
    assert stored.raw_path == str(copy_path.parent / "raw.pdf")


def test_an_unidentifiable_copy_keeps_its_name(database: Database, tmp_path: Path) -> None:
    """认不出就不猜：猜错的后缀会让重新转换用错解析器，把正文换成另一种东西。"""
    document, copy_path = _seed_copy(database, tmp_path, BINARY_BYTES)

    report = _reconcile(database)

    assert (report.renamed, report.baselined) == (0, 0)
    assert report.unidentified == 1
    assert copy_path.exists()
    stored = DocumentStore(database).get(document.id)
    assert stored is not None
    assert stored.raw_path == str(copy_path)
    assert stored.original_revision is None


def test_a_linked_document_with_a_leftover_copy_is_left_alone(
    database: Database, tmp_path: Path
) -> None:
    """链接型可能还留着一份没人读的旧副本（历史遗留）。那份字节已经不是原件了 ——
    给它改名、给它记基线，只会制造"这就是原件"的新错觉。"""
    document, copy_path = _seed_copy(
        database, tmp_path, PDF_BYTES, source_path=str(tmp_path / "用户自己的.pdf")
    )

    report = _reconcile(database)

    assert (report.renamed, report.baselined) == (0, 0)
    assert copy_path.exists()
    stored = DocumentStore(database).get(document.id)
    assert stored is not None
    assert stored.raw_path == str(copy_path)


def test_an_existing_file_is_never_overwritten(database: Database, tmp_path: Path) -> None:
    """目标名字已经被占：什么都不做，也不改记录 —— 宁可这份点不动，也不能盖掉字节。"""
    document, copy_path = _seed_copy(database, tmp_path, PDF_BYTES)
    occupied = copy_path.parent / "raw.pdf"
    occupied.write_bytes(b"%PDF-1.7\nalready here")

    report = _reconcile(database)

    assert (report.renamed, report.failed) == (0, 1)
    assert occupied.read_bytes() == b"%PDF-1.7\nalready here"
    assert copy_path.exists()
    stored = DocumentStore(database).get(document.id)
    assert stored is not None
    assert stored.raw_path == str(copy_path)


def test_a_missing_copy_is_counted_instead_of_crashing(
    database: Database, tmp_path: Path
) -> None:
    """记录里有、磁盘上没了：数与状态都说得清，不能让它把启动搞崩。"""
    _, copy_path = _seed_copy(database, tmp_path, PDF_BYTES)
    copy_path.unlink()

    report = _reconcile(database)

    assert (report.renamed, report.missing) == (0, 1)


def test_a_second_run_has_nothing_left_to_do(database: Database, tmp_path: Path) -> None:
    """对账每次启动都跑：第二遍必须无话可说，否则"补基线"就变成了每启动一次刷一次。"""
    _seed_copy(database, tmp_path, PDF_BYTES)

    first = _reconcile(database)
    second = _reconcile(database)

    assert (first.renamed, first.baselined) == (1, 1)
    assert (second.renamed, second.baselined) == (0, 0)


def test_recording_a_copy_never_turns_it_into_a_linked_original(
    database: Database, tmp_path: Path
) -> None:
    """`update_stored_copy` 绝不能写 `source_path`。

    写进去这篇文档就变成"链接型指向我们自己的目录"：重新定位会以为用户绑了文件，
    状态判断也会改走链接那条路 —— 一切都开始说谎。
    """
    document, copy_path = _seed_copy(database, tmp_path, PDF_BYTES)
    store = DocumentStore(database)

    store.update_stored_copy(document.id, raw_path=str(copy_path), original_revision="9:9")

    stored = store.get(document.id)
    assert stored is not None
    assert stored.source_path is None
    assert stored.raw_path == str(copy_path)
    assert stored.original_revision == "9:9"

    linked, _ = _seed_copy(
        database,
        tmp_path,
        PDF_BYTES,
        document_id="doc-linked-leftover",
        source_path=str(tmp_path / "用户自己的.pdf"),
    )
    store.update_stored_copy(linked.id, original_revision="7:7")
    still_linked = store.get(linked.id)
    assert still_linked is not None
    assert still_linked.source_path == str(tmp_path / "用户自己的.pdf")


@pytest.mark.parametrize(
    ("payload", "expected_name"),
    (
        (PDF_BYTES, "raw.pdf"),
        (DOCX_BYTES, "raw.docx"),
        (MARKDOWN_BYTES, "raw.md"),
        (HTML_BYTES, "raw.html"),
        (BINARY_BYTES, None),
    ),
)
def test_sniffing_picks_the_formats_own_suffix(
    tmp_path: Path, payload: bytes, expected_name: str | None
) -> None:
    """名字只能取自**格式自己声明的**后缀。

    挑一个"打开得了的"后缀（比如给一份 markdown 副本起名 `.txt`）会让重新转换当场报
    "不支持的来源" —— 它是按后缀反解格式的。
    """
    path = tmp_path / "raw.bin"
    path.write_bytes(payload)

    sniffed = sniff_original_format(path, builtin_registry())

    assert (stored_original_name(sniffed) if sniffed is not None else None) == expected_name
