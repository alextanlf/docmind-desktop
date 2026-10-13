"""链接型原件：只有主进程签过名的路径才读得到。

这一组用例守的是"渲染层不能指名任意文件"这条性质。它们会红的方式很具体：
把签名校验去掉、把 domain 混用、或者让"路径不存在"与"签名不对"给出不同的回答。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.path_tokens import DIRECTORY_DOMAIN, FILE_DOMAIN, path_token
from app.document.sources import LinkedFileStore

SECRET = "linked-file-test-token"


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(session_token=SECRET, data_dir=tmp_path / "data", environment="test")


@pytest.fixture
def store(settings: AppSettings) -> LinkedFileStore:
    return LinkedFileStore(settings)


@pytest.fixture
def original(tmp_path: Path) -> Path:
    path = tmp_path / "论文" / "attention.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return path


def test_reads_a_signed_path_and_remembers_where_it_came_from(
    store: LinkedFileStore, original: Path
) -> None:
    token = path_token(SECRET, FILE_DOMAIN, str(original))

    document = store.load(str(original), token, display_name="attention.pdf")

    assert document.raw_bytes == b"%PDF-1.4\n%%EOF\n"
    assert document.source_path == original
    assert document.media_type == "application/pdf"


def test_source_url_never_carries_the_absolute_path(
    store: LinkedFileStore, original: Path
) -> None:
    """绝对路径不许进预览、chunk 或引用 —— 与暂存路径同一条约束。

    source_url 会写进每个 chunk、并出现在聊天引用里，把家目录路径带进去等于把它
    抄进每一个引用。
    """
    token = path_token(SECRET, FILE_DOMAIN, str(original))

    document = store.load(str(original), token, display_name="attention.pdf")

    assert str(original) not in document.source_url
    assert str(original.parent) not in document.source_url
    assert document.source_url == "linked://attention.pdf"


def test_unsigned_path_is_rejected(store: LinkedFileStore, original: Path) -> None:
    with pytest.raises(DomainError) as error:
        store.load(str(original), None)

    assert (error.value.code, error.value.status_code, error.value.retryable) == (
        "SOURCE_UNSUPPORTED",
        400,
        False,
    )


def test_token_for_another_path_does_not_open_this_one(
    store: LinkedFileStore, original: Path, tmp_path: Path
) -> None:
    """整个机制要防的就是这件事：拿一个文件的能力去读另一个文件。"""
    secret_file = tmp_path / "id_rsa"
    secret_file.write_text("PRIVATE KEY", encoding="utf-8")
    other_token = path_token(SECRET, FILE_DOMAIN, str(original))

    with pytest.raises(DomainError) as error:
        store.load(str(secret_file), other_token)

    assert error.value.code == "SOURCE_UNSUPPORTED"


def test_directory_grant_cannot_be_used_as_a_file_grant(
    store: LinkedFileStore, original: Path
) -> None:
    """两个 domain 必须各管各的，否则"授权一个目录"等于授权目录下任意路径。"""
    token = path_token(SECRET, DIRECTORY_DOMAIN, str(original))

    with pytest.raises(DomainError):
        store.load(str(original), token)


def test_an_unverifiable_request_reveals_nothing_about_the_path(
    store: LinkedFileStore, tmp_path: Path, original: Path
) -> None:
    """伪造签名时，**路径存不存在**不许影响回答，否则这个接口成了存在性探测器。

    注意边界画在哪：签名校验发生在任何文件系统访问**之前**，所以没有签名的人问什么
    都是同一个回答。签名有效之后（只有主进程签得出来）才可以说得更具体 —— 见下一条。
    """
    missing = tmp_path / "not-here.pdf"

    with pytest.raises(DomainError) as existing_error:
        store.load(str(original), "forged")
    with pytest.raises(DomainError) as missing_error:
        store.load(str(missing), "forged")

    assert existing_error.value.code == missing_error.value.code
    assert existing_error.value.message == missing_error.value.message
    assert existing_error.value.status_code == missing_error.value.status_code
    assert "不存在" not in missing_error.value.message
    assert "不可读" not in missing_error.value.message


def test_a_signed_but_unreadable_original_says_so(
    store: LinkedFileStore, tmp_path: Path
) -> None:
    """签名有效但文件没了 —— 这时可以说得具体一点。

    只有主进程签得出这个 token（等价于"用户真的选过这个路径"），所以这条更具体的信息
    只可能被有权读它的人看到。用一点措辞换用户看得懂的报错是划算的。
    """
    missing = tmp_path / "moved-away.pdf"

    with pytest.raises(DomainError) as error:
        store.load(str(missing), path_token(SECRET, FILE_DOMAIN, str(missing)))

    assert (error.value.code, error.value.message) == ("SOURCE_UNSUPPORTED", "原件不可读")


def test_relative_path_is_rejected_even_with_a_valid_token(
    store: LinkedFileStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """主进程给的永远是对话框返回的绝对路径；相对路径说明请求不是它发的。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("# 相对路径", encoding="utf-8")
    token = path_token(SECRET, FILE_DOMAIN, "notes.md")

    with pytest.raises(DomainError) as error:
        store.load("notes.md", token)

    assert error.value.code == "SOURCE_UNSUPPORTED"


def test_directory_is_not_readable_as_a_document(
    store: LinkedFileStore, tmp_path: Path
) -> None:
    """签名保护的是路径，不保证它是个文件；目录必须被挡在 fstat 那一步。"""
    directory = tmp_path / "资料"
    directory.mkdir()
    token = path_token(SECRET, FILE_DOMAIN, str(directory))

    with pytest.raises(DomainError):
        store.load(str(directory), token)


def test_size_ceiling_still_applies(
    settings: AppSettings, tmp_path: Path, original: Path
) -> None:
    """链接型不能成为绕过大小限制的后门：上限来自配置，与暂存那条路同一份判断。"""
    store = LinkedFileStore(settings)
    oversized = tmp_path / "big.md"
    oversized.write_bytes(b"x" * (settings.html_markdown_max_bytes + 1))
    token = path_token(SECRET, FILE_DOMAIN, str(oversized))

    with pytest.raises(DomainError) as error:
        store.load(str(oversized), token)

    assert (error.value.code, error.value.status_code) == ("DOCUMENT_TOO_LARGE", 413)


def test_unsupported_extension_is_rejected(
    store: LinkedFileStore, tmp_path: Path
) -> None:
    path = tmp_path / "archive.zip"
    path.write_bytes(b"PK\x03\x04")
    token = path_token(SECRET, FILE_DOMAIN, str(path))

    with pytest.raises(DomainError):
        store.load(str(path), token)


def test_symlink_chosen_by_the_user_is_followed(
    store: LinkedFileStore, original: Path, tmp_path: Path
) -> None:
    """用户在对话框里选了一个符号链接，就该读它指向的东西。

    这与暂存目录相反（那边用 O_NOFOLLOW 拒绝符号链接）：区别在于**谁选的**。
    暂存目录是"别人可能往里放东西"的地方，而这里是他自己指的一个路径。
    """
    link = tmp_path / "桌面上的快捷方式.pdf"
    link.symlink_to(original)
    token = path_token(SECRET, FILE_DOMAIN, str(link))

    document = store.load(str(link), token)

    assert document.raw_bytes == b"%PDF-1.4\n%%EOF\n"
    assert document.source_path == link
