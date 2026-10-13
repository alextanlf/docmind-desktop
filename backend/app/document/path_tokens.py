"""路径能力（capability）：让后端只读"用户在对话框里真的选过"的那些文件。

## 为什么需要它

导入改成直接读磁盘上的原件之后，后端的接口就必须接受一个**绝对路径**。但今天有一条
必须守住的性质：**渲染层不能指名任意文件**。现成的两道闸门是 —— 后端的暂存标识必须是
规范 UUID（`_validated_staged_path`），IPC 面是一份固定的 channel 清单、没有通用透传 ——
渲染层因此没有任何办法让后端去读一个它自己挑的路径。插件的 UI 就是渲染层代码，这条性质
不是洁癖：一个被污染的渲染层若能指名路径，就能把 `~/.ssh/id_rsa` 当文档导入再读回来。

所以路径必须**带着主进程的签名**进来。主进程只对"文件对话框真的返回过的路径"签名，
而签名密钥（`session_token`）是主进程生成、经环境变量注入后端的，渲染层拿不到。

## 为什么签原始字符串，两边都不做规范化

跨语言做路径规范化必然出事：TS 的 `path.resolve` 与 Python 的 `Path.resolve` 在符号链接、
大小写、以及 macOS 的 Unicode 归一化（APFS 上文件名常以 NFD 存）上并不一致。
一旦两边对同一个路径算出不同的字符串，轻则"签名对不上"，重则**两个不同的字符串被规范成
同一个路径**，于是签名保护的就不再是我们以为的那个文件。

因此：签名与校验都用**同一个不可变字符串**的 UTF-8 字节。规范化只发生在校验通过之后，
而且只用于安全检查（是不是普通文件、有没有逃出授权目录），不参与签名。

## 不设过期

token 的生命周期等于"用户授权了这个路径"这件事本身 —— 链接型原件就是要长期绑定的。
泄露一个 token 的后果被限制在那一个路径上，且它本来就属于这台机器的这个用户。
"""

from __future__ import annotations

import base64
import hashlib
import hmac

#: 签名域。分开是为了让"授权一个目录"不能被当成"授权某个文件路径"使用。
FILE_DOMAIN = "linked-file"
DIRECTORY_DOMAIN = "linked-directory"


def path_token(secret: str, domain: str, path: str) -> str:
    """算出 ``path`` 在 ``domain`` 下的签名（base64url，无填充）。"""
    # 编码必须是 utf-8，且与 JS 侧的 Buffer.from(..., "utf-8") 对齐 —— 这是跨语言契约的
    # 一部分，不是可以随手换的实现细节。str.encode() 的默认编码就是 utf-8，故不写参数。
    message = f"{domain}:{path}".encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def path_token_matches(secret: str, domain: str, path: str, token: str | None) -> bool:
    """常量时间比较。``token`` 缺失或形态不对一律 False，不抛异常。

    调用方**不要**用返回值区分"路径不存在"与"签名不对"：那会让这个接口变成一个任意路径
    的存在性探测器。两种情况对外都是同一句"不支持的文档来源"。
    """
    if not isinstance(token, str) or not token:
        return False
    try:
        expected = path_token(secret, domain, path)
    except (UnicodeEncodeError, ValueError):
        return False
    return hmac.compare_digest(expected, token)
