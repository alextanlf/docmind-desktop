"""随应用分发的「第一方网页来源」种子表。

为什么需要它：语料推导的其它信号都有前提 —— 从文档里挖域名要先有文档，从上游搜索
结果挖域名要先能搜。而一个刚装好的 DocMind 三样都没有（实测：3 篇本地文档、0 次
网页抓取、0 次联网搜索）。所以冷启动必须由**不依赖任何已有数据、也不依赖网络**的
信号承担，这就是这张表。

三条纪律，都照抄仓库里已经用过的那套（见 `MODEL_CATALOG_DOC_SOURCES` /
`test_model_capabilities.py` 的陈旧度闸门）：

1. **每条都要有证据。** `evidence` 说的是"为什么这是第一方来源"，可被人复核；
   说不出理由的域名不该出现在这里。
2. **每条都要有核对日期。** 超过 `SEED_MAX_AGE_DAYS` 就构建失败 —— 厂商会改站、
   换域名、下掉文档，而"有没有人去看过"是唯一能离线检查的东西。
3. **只收第一方。** 官方文档、规范、包索引、发布说明；**不收**聚合站、问答站、
   镜像站。技术问题上一手来源比高赞答案可靠，而聚合站的内容随时会消失或改版。

⚠️ 这里只负责"提名"，不负责"能抓到"。可达性是运行时的事（有的域名在本地网络根本
连不上，见 `document/reachability.py`），两者不能混：把可达性写进种子表，等于让
一张静态表假装知道用户此刻的网络。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import urlsplit

#: 来源类型。**封闭集合**：多一个取值就要多一处渲染文案，且它决定用户看到的分组。
SourceKind = Literal[
    "official_docs",  # 产品/语言的官方文档
    "spec",  # 规范或标准正文
    "package_index",  # 包索引（版本与依赖事实以此为准）
    "vendor_api_docs",  # 厂商 API 文档
    "release_notes",  # 发布说明 / 变更日志
]


@dataclass(frozen=True)
class SeedSource:
    url: str
    label: str
    kind: SourceKind
    #: 为什么这是第一方来源。可复核的一句话，不是营销词。
    evidence: str


@dataclass(frozen=True)
class SeedTopic:
    key: str
    label: str
    #: 命中这些词就算这个话题相关。中英混排是必要的：用户问中文，文档是英文。
    keywords: tuple[str, ...]
    #: 「某人最后一次带着依据看过这一行」的日期（YYYY-MM-DD），不是"内容绝对正确"。
    reviewed_at: str
    sources: tuple[SeedSource, ...]


#: 核对周期上限。文档站不像模型 id 那样按月改名，一年足够；再长就等于没有闸门。
SEED_MAX_AGE_DAYS = 365


SEED_TOPICS: dict[str, SeedTopic] = {
    "python": SeedTopic(
        key="python",
        label="Python",
        keywords=("python", "cpython", "pip", "pypi", "标准库", "虚拟环境"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://docs.python.org/3/",
                label="Python 官方文档",
                kind="official_docs",
                evidence="docs.python.org 是 python.org 的子域，语言与标准库的权威来源",
            ),
            SeedSource(
                url="https://pypi.org/",
                label="PyPI",
                kind="package_index",
                evidence="Python 官方包索引；版本号与依赖关系以它为准，不以博客为准",
            ),
        ),
    ),
    "web_platform": SeedTopic(
        key="web_platform",
        label="Web 平台（HTML / CSS / JS）",
        keywords=("html", "css", "javascript", "dom", "浏览器", "前端", "web api"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://developer.mozilla.org/",
                label="MDN Web Docs",
                kind="official_docs",
                evidence="Mozilla 维护的 Web 平台文档；浏览器兼容性表的常用第一手来源",
            ),
            SeedSource(
                url="https://html.spec.whatwg.org/",
                label="HTML 规范（WHATWG）",
                kind="spec",
                evidence="WHATWG 是 HTML 的规范制定方；解析与语义的争议以规范正文为准",
            ),
        ),
    ),
    "http_specs": SeedTopic(
        key="http_specs",
        label="HTTP 与互联网规范",
        keywords=("http", "rfc", "tls", "rest", "状态码", "缓存头"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://www.rfc-editor.org/",
                label="RFC 编辑器",
                kind="spec",
                evidence="RFC 正文的发布方；协议细节以原文为准，二手解读经常漏掉 MUST/NOT",
            ),
        ),
    ),
    "sqlite": SeedTopic(
        key="sqlite",
        label="SQLite",
        keywords=("sqlite", "sqlite3", "fts5", "vec0", "wal"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://sqlite.org/docs.html",
                label="SQLite 官方文档",
                kind="official_docs",
                evidence="sqlite.org 是 SQLite 项目自己的站点（D. Richard Hipp 团队）",
            ),
        ),
    ),
    "postgres": SeedTopic(
        key="postgres",
        label="PostgreSQL",
        keywords=("postgres", "postgresql", "pgsql", "pgvector"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://www.postgresql.org/docs/",
                label="PostgreSQL 官方文档",
                kind="official_docs",
                evidence="postgresql.org 是 PostgreSQL 全球开发组的站点",
            ),
        ),
    ),
    "node": SeedTopic(
        key="node",
        label="Node.js",
        keywords=("node", "nodejs", "npm", "pnpm", "vite", "esm"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://nodejs.org/docs/latest/api/",
                label="Node.js API 文档",
                kind="official_docs",
                evidence="nodejs.org 是 Node.js 官方站点，API 文档随版本发布",
            ),
        ),
    ),
    "react": SeedTopic(
        key="react",
        label="React",
        keywords=("react", "jsx", "hooks", "usestate", "useeffect", "react-dom"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://react.dev/",
                label="React 官方文档",
                kind="official_docs",
                evidence="react.dev 是 React 团队在 hooks 之后重写的官方文档站",
            ),
        ),
    ),
    "electron": SeedTopic(
        key="electron",
        label="Electron",
        keywords=("electron", "electron-vite", "ipc", "preload", "contextbridge"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://www.electronjs.org/docs/latest/",
                label="Electron 官方文档",
                kind="official_docs",
                evidence="electronjs.org 是 OpenJS 基金会下的 Electron 官方站点",
            ),
        ),
    ),
    "containers": SeedTopic(
        key="containers",
        label="容器与编排",
        keywords=("docker", "dockerfile", "kubernetes", "k8s", "container", "compose"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://docs.docker.com/",
                label="Docker 官方文档",
                kind="official_docs",
                evidence="docs.docker.com 是 Docker 公司的官方文档站",
            ),
            SeedSource(
                url="https://kubernetes.io/docs/",
                label="Kubernetes 官方文档",
                kind="official_docs",
                evidence="kubernetes.io 是 CNCF 项目的官方站点",
            ),
        ),
    ),
    "git": SeedTopic(
        key="git",
        label="Git",
        keywords=("git", "rebase", "cherry-pick", "worktree", "submodule"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://git-scm.com/doc",
                label="Git 官方文档",
                kind="official_docs",
                evidence="git-scm.com 是 Git 项目自己的站点（Pro Git 与手册的发布方）",
            ),
        ),
    ),
    # 下面这一组是本项目**自己预设的那几家厂商**的文档。它们的价值不在于"技术性
    # 强"，而在于 DocMind 的用户问的大概率就是这些模型的用法与限制 —— 而模型目录
    # 里已经登记了这些厂商的公开文档页（`MODEL_CATALOG_DOC_SOURCES`，2026-10-10
    # 逐条实测返回 200），这里复用的是同一批地址，不是另猜一套。
    "vendor_deepseek": SeedTopic(
        key="vendor_deepseek",
        label="DeepSeek 文档",
        keywords=("deepseek", "deepseek-flash", "deepseek-v4"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://api-docs.deepseek.com/",
                label="DeepSeek API 文档",
                kind="vendor_api_docs",
                evidence="api-docs.deepseek.com 是 DeepSeek 官方文档域（本机实测可达）",
            ),
        ),
    ),
    "vendor_qwen": SeedTopic(
        key="vendor_qwen",
        label="通义千问文档",
        keywords=("qwen", "通义", "dashscope", "百炼", "qwen3"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://help.aliyun.com/zh/document_detail/2867560.html",
                label="通义千问模型文档（阿里云）",
                kind="vendor_api_docs",
                evidence="与 `MODEL_CATALOG_DOC_SOURCES` 同源，2026-10-10 实测 200",
            ),
        ),
    ),
    "vendor_kimi": SeedTopic(
        key="vendor_kimi",
        label="Kimi 文档",
        keywords=("kimi", "moonshot", "月之暗面"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://platform.moonshot.cn/docs/guide/use-thinking-models",
                label="Kimi 开放平台文档",
                kind="vendor_api_docs",
                evidence="与 `MODEL_CATALOG_DOC_SOURCES` 同源，2026-10-10 实测 200",
            ),
        ),
    ),
    "vendor_glm": SeedTopic(
        key="vendor_glm",
        label="智谱 GLM 文档",
        keywords=("glm", "智谱", "bigmodel", "z.ai", "zhipu"),
        reviewed_at="2026-10-11",
        sources=(
            SeedSource(
                url="https://docs.bigmodel.cn/cn/guide/start/concept-param/",
                label="智谱开放平台文档（国内）",
                kind="vendor_api_docs",
                evidence="与 `MODEL_CATALOG_DOC_SOURCES` 同源，2026-10-10 实测 200",
            ),
            SeedSource(
                url="https://docs.z.ai/guides/llm/glm-5.3",
                label="智谱文档（海外站）",
                kind="vendor_api_docs",
                evidence="海外站与国内站参数口径不同，两者都登记以便按可达性二选一",
            ),
        ),
    ),
}


def seed_review_today() -> date:
    """核对口径的「今天」，固定 UTC。

    与 `catalog_review_today` 同一个理由：闸门必须在所有人机器上同一天响，否则
    "这家该核对了"会因人而异。
    """
    return datetime.now(tz=UTC).date()


def stale_seed_topics(today: date | None = None) -> list[tuple[str, str, int, int]]:
    """超过核对周期的话题，返回 ``(topic, 核对日期, 已过天数, 周期上限)``。

    `today` 可注入，好让"闸门本身会不会响"被测试钉住 —— 否则它要么永远返回空列表
    （装饰），要么某天毫无预兆地对所有人变红。
    """
    current = today or seed_review_today()
    stale: list[tuple[str, str, int, int]] = []
    for key, topic in SEED_TOPICS.items():
        age = (current - date.fromisoformat(topic.reviewed_at)).days
        if age > SEED_MAX_AGE_DAYS:
            stale.append((key, topic.reviewed_at, age, SEED_MAX_AGE_DAYS))
    return stale


def seed_domain(url: str) -> str:
    """种子来源的域名（小写、去掉 `www.`）。种子里写的是 URL，比较时用域名。"""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def match_topics(text: str, *, limit: int = 3) -> list[SeedTopic]:
    """按关键词命中度挑出相关话题，命中多的在前。

    这是**廉价的离线近似**，不是语义检索：用户问"sqlite 的 FTS5 怎么建索引"时能
    命中 `sqlite`，问一个种子表里没有的领域就什么都不返回 —— 那种情况交给模型提名
    与用户自己贴地址，而不是硬凑一个不相关的来源。
    """
    haystack = text.casefold()
    if not haystack.strip():
        return []
    scored: list[tuple[int, int, SeedTopic]] = []
    for index, topic in enumerate(SEED_TOPICS.values()):
        hits = sum(1 for keyword in topic.keywords if keyword.casefold() in haystack)
        if hits:
            # 次级键是声明顺序：命中数相同的两个话题每次排序结果必须一致。
            scored.append((hits, index, topic))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [topic for _, _, topic in scored[:limit]]
