"""路径签名的行为与**跨语言一致性**。

最后一条测试用的是与 `electron/tests/main/path-tokens.test.ts` **逐字相同的向量**。
两边各自实现 HMAC，任何一边改了算法、消息构造或编码，都会有一边的测试变红 ——
这是本仓库对"跨语言契约两边各守一半"的既有做法（见 contracts.test.ts 的注释）。
"""

from __future__ import annotations

import pytest

from app.document.path_tokens import (
    DIRECTORY_DOMAIN,
    FILE_DOMAIN,
    path_token,
    path_token_matches,
)

#: 与 TS 侧共用的向量。这三个字符串别改：改了等于换了一套契约。
SHARED_SECRET = "test-session-token"
SHARED_PATH = "/Users/someone/论文/attention.pdf"
SHARED_TOKENS = {
    FILE_DOMAIN: "Ls7zxuVJfxiiBtBRugUXNpTRO7c7x3K6zc35foYy-B4",
    DIRECTORY_DOMAIN: "xtNXjE18Bv40nRGW7ll7twLbjiX7dG-ohp38f0TSrfg",
}


def test_matches_the_token_the_client_computes() -> None:
    for domain, expected in SHARED_TOKENS.items():
        assert path_token(SHARED_SECRET, domain, SHARED_PATH) == expected


def test_token_is_urlsafe_and_unpadded() -> None:
    token = path_token("s", FILE_DOMAIN, "/tmp/a b+c/d")

    assert "=" not in token
    assert all(character.isalnum() or character in "-_" for character in token)


def test_domains_do_not_interchange() -> None:
    """授权一个目录不能当成授权某个文件路径来用。"""
    secret, path = "s", "/Users/someone/资料"
    directory_token = path_token(secret, DIRECTORY_DOMAIN, path)

    assert path_token_matches(secret, DIRECTORY_DOMAIN, path, directory_token)
    assert not path_token_matches(secret, FILE_DOMAIN, path, directory_token)


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        # 换一个字符的近似值（长度对、内容错）
        "Ls7zxuVJfxiiBtBRugUXNpTRO7c7x3K6zc35foYy-B5",
        # 另一个密钥签出来的同一个路径
        path_token("another-secret", FILE_DOMAIN, SHARED_PATH),
    ],
)
def test_forged_or_missing_tokens_are_rejected(token: str | None) -> None:
    assert not path_token_matches(
        SHARED_SECRET, FILE_DOMAIN, SHARED_PATH, token
    )


def test_a_token_for_one_path_does_not_open_another() -> None:
    """这就是整个机制要防的事：拿一个文件的能力去读另一个文件。"""
    secret = SHARED_SECRET
    signed = path_token(secret, FILE_DOMAIN, "/Users/someone/公开.pdf")

    assert path_token_matches(secret, FILE_DOMAIN, "/Users/someone/公开.pdf", signed)
    assert not path_token_matches(secret, FILE_DOMAIN, "/Users/someone/.ssh/id_rsa", signed)


def test_path_without_a_token_is_rejected() -> None:
    assert not path_token_matches("s", FILE_DOMAIN, "/etc/passwd", None)
