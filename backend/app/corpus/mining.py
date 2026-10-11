"""从用户**已有的数据**里挖来源站点。

这是推导层的第三条信号（`candidates.Signal.DOCUMENTS` / `CRAWL_HISTORY`），也是唯一
一条"不需要问任何人"的信号：文档里出现的网址、以及之前抓过的网址，都在库里躺着。

两个诚实的前提：

1. **它可能什么都没有。** 一个刚装好的 DocMind 里 3 篇文档全是本地暂存目录导入的
   （`staged://…`），挖出来是零。所以它只能是**增益**，不能承担冷启动 —— 那是种子表与
   模型提名的事。
2. **它不是权威。** 文档引用过的站点只说明"这份文档提过它"，不说明它是一手来源。
   所以这些候选的 `verified` 是 False，排序权重也低于种子表（见 `candidates.py`）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: 从任意字段里抠出一个网址。`source_identity` 是形如 `web:https://host/path` 的复合
#: 标识，直接送进 `normalize_domain` 会因为 scheme 是 `web` 而取不到主机名。
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

#: 一次最多采纳多少个网址。候选清单本来就有上限，取太多只是让推导多扫一遍。
MAX_MINED_URLS = 500


def url_like(value: object) -> str | None:
    """从一个字段值里取出可用的网址或域名。

    - `web:https://docs.test/a` → `https://docs.test/a`
    - `https://docs.test/a` → 原样
    - `docs.test` → 原样（`normalize_domain` 接受裸域名）
    - `staged://<id>` / `staged-collection://…` → None（本地暂存，不是站点）
    - None / 空串 → None
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    match = _URL.search(text)
    if match is not None:
        return match.group(0)
    # 没有 http(s) 前缀时，只接受裸域名形态（含点、无空格）。`staged://x` 这类带
    # 别的 scheme 的值会落到这里，靠"含冒号"排除掉。
    if ":" in text or " " in text or "." not in text:
        return None
    return text


def collect(values: Iterable[object], *, limit: int = MAX_MINED_URLS) -> list[str]:
    """把一批字段值收成去重后的网址列表，保持出现顺序。"""
    found: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = url_like(value)
        if url is None or url in seen:
            continue
        seen.add(url)
        found.append(url)
        if len(found) >= limit:
            break
    return found
