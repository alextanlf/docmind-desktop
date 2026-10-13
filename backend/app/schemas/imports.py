from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.schemas.common import WireModel

#: 导入来源的类别。**只在这里声明一次**：它原本散在 `SourceRef` 与 `SourcePreview` 两处，
#: 于是加一种来源时很容易只改一处 —— 那样会在运行时抛 ValidationError（`linked_file`
#: 加进来时就真的踩到了），而不是在测试里红。客户端的枚举由
#: `electron/tests/shared/contracts.test.ts` 对着这行源码断言，两边因此不会各说各话。
SOURCE_KINDS = ("url", "staged_file", "linked_file")
SourceKind = Literal["url", "staged_file", "linked_file"]


class SourceRef(WireModel):
    kind: SourceKind
    #: url 是地址；staged_file 是暂存标识（UUID）；linked_file 是用户磁盘上的绝对路径。
    value: str
    #: 用户在选择器里看到的文件名（`报告.pdf`）。
    #:
    #: 暂存副本按 UUID 命名（防路径注入），所以这个名字只在客户端手里；上游不带标题的
    #: 格式（PDF、docx）失去它就只剩 UUID 当标题 —— 实测本机 3 篇里 2 篇如此。
    #: 只用于显示：后端取 basename，绝不进入路径、chunk 或引用。
    name: str | None = None
    #: `linked_file` 专用：主进程对 `value`（绝对路径）的签名。
    #:
    #: 后端只读签名对得上的路径，于是**渲染层依然不能指名任意文件**（插件的 UI 就是
    #: 渲染层代码）。两边各一半的测试守着"跨语言逐字节一致"这件事，见
    #: `document/path_tokens.py` 与 `electron/main/path-tokens.ts` 的模块注释。
    path_token: str | None = None


class SourceFormatView(WireModel):
    """One importable format, as the client needs to render a file picker.

    Declared by the backend so the dialog's filters, its size ceiling and the
    label on the button all follow from what is actually installed — including
    formats a plugin contributes. The client holds no list of its own.
    """

    name: str
    label: str
    extensions: list[str]
    media_type: str
    #: Ceiling in bytes for a single file of this format, from configuration.
    max_bytes: int


class SourcePreview(WireModel):
    title: str
    source_kind: SourceKind
    source_url: str | None = None
    media_type: str
    size_bytes: int
    fingerprint: str
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_document(
        cls, source_kind: SourceKind, document: DownloadedDocument
    ) -> SourcePreview:
        title = document.title
        if document.media_type == "text/markdown":
            for line in document.raw_bytes.decode("utf-8-sig").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip() or title
                    break
        return cls(
            title=title,
            source_kind=source_kind,
            source_url=document.source_url,
            media_type=document.media_type,
            size_bytes=len(document.raw_bytes),
            fingerprint=hashlib.sha256(document.raw_bytes).hexdigest(),
            warnings=[],
        )


class DownloadedDocument(WireModel):
    title: str
    source_url: str
    media_type: str
    raw_bytes: bytes
    local_path: Path | None = None
    #: 原件的**归属**：有值表示字节来自用户磁盘上的这个位置，应用不留副本。
    #:
    #: 与 `local_path` 的区别是"谁的东西"：暂存副本是应用自己拷进来的临时文件（用完就删），
    #: 而这里是用户的文件 —— 只读、永不删、永不改，文档被删时也不许碰它。
    #: 链接型文档据此长期绑定原件（原件视图、重新转换都读它）。
    source_path: Path | None = None


class ParsedFigure(WireModel):
    """一张插图，以及把它展示出来所需的一切。

    ``asset`` 是落盘后的文件名（相对文档自己的附件目录）。用内容摘要命名而不是自增序号：
    同一次导入重复执行时文件名保持一致，不会在磁盘上留下两份同样的图。

    ``bbox`` 是这张图在页面上的位置（PDF 点，原点左上）。原件是 PDF 时前端据此直接跳到
    那一页并框出这块区域 —— 没有它，用户得自己在一份 500 页的扫描件里找。
    """

    asset: str
    page_number: int | None = None
    #: 图内已有的文字（图注、轴名、刻度、标注）。空字符串表示这张图只能靠识别补内容。
    caption: str = ""
    width: int = 0
    height: int = 0
    bbox: tuple[float, float, float, float] | None = None
    #: 整页扫描件的整页渲染图，而不是页内某一张插图。
    full_page: bool = False
    #: 这张图来自哪个远程地址（网页插图才有）。
    #:
    #: 有地址、没有 `payload` 就表示「还没下载」—— 导入服务据此把待抓取的图挑出来。
    #: 解析阶段刻意不做网络访问：那会让同步的解析器变成异步的，牵动整条链路。
    source_url: str = ""
    #: 图内识别出来的文字。
    #:
    #: 与 `caption` 分开：`caption` 是图注/替代文字（人工写的、给用户看的），`text` 是
    #: 识别结果（机器产出的、用来并入正文参与检索）。两者都可能为空，语义也不同。
    #: 不参与序列化：它的去处是正文，客户端不需要再拿一份。
    text: str = Field(default="", exclude=True)
    #: 抓取失败的原因。只在诊断时有用，不参与序列化 —— 客户端拿到一个空图没有意义。
    failure: str = Field(default="", exclude=True)
    #: 渲染出来的 PNG 字节，只在解析到落盘之间存活。
    #:
    #: 不参与序列化：它可能上百 KB，塞进 `images_json` 会让每个 chunk 行都背着一份图片；
    #: 写盘之后它的使命就结束了，chunk 里只需要留下文件名。
    payload: bytes = Field(default=b"", exclude=True)


class ParsedSection(WireModel):
    heading_path: list[str]
    markdown: str
    page_number: int | None = None
    #: 这一节关联的插图。挂在 section 上而不是文档上，是为了保住「这张图在第 3 页第 2 节」
    #: 这个定位信息 —— 丢了它，引用面板就无法把图和它所在的正文对上。
    figures: list[ParsedFigure] = Field(default_factory=list)


class ParsedDocument(WireModel):
    title: str
    markdown: str
    source_url: str
    sections: list[ParsedSection]


class DocumentChunkDraft(WireModel):
    text: str
    section_path: str
    page_number: int | None = None
    chunk_index: int
    token_count: int
    source_url: str
    #: 这个 chunk 关联的插图。切块时从所属 section 逐个带下来，让「正文片段」和
    #: 「它配的图」在检索结果里始终成对出现。
    figures: list[ParsedFigure] = Field(default_factory=list)


class ImportCreateRequest(WireModel):
    source: SourceRef
    repository_id: str
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    duplicate_decision: Literal["skip", "update"] | None = None


class ImportJobView(WireModel):
    id: str
    source: SourceRef
    repository_id: str
    state: Literal[
        "pending", "parsing", "uploading", "indexing", "completed", "failed", "cancelled"
    ]
    current_stage: str | None = None
    progress: int
    message: str
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool
    document_id: str | None = None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime
