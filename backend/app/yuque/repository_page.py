from __future__ import annotations

import json
from urllib.parse import urlparse

from app.schemas.remote import RemoteDocument
from app.yuque.base_page import BasePage, maybe_await

#: 语雀把当前书籍的目录内嵌在页面里。这里取的就是**渲染用的那份真实数据**，
#: 而不是去猜 DOM 嵌套 —— 猜出来的层级看着像棵树，按目录移除时却会漏掉文档，
#: 而"漏掉"在清理场景里等于没清。
#:
#: 形状容错且**取不到就返回 null**：语雀的 appData 各版本字段名不同，任何对不上
#: 的地方都退化成"没有路径"（界面显示平铺），绝不编一个层级出来。
_CATALOG_SCRIPT = """
(() => {
  const data = window.appData || {};
  const book = data.book || data.bookData || data.bookInfo || {};
  const candidates = [data.toc, book.toc, data.catalog && data.catalog.toc, data.tocData];
  const toc = candidates.find((value) => Array.isArray(value));
  if (!toc) return null;
  const out = [];
  const walk = (nodes, parents) => {
    for (const node of nodes) {
      if (!node || typeof node !== 'object') continue;
      const title = typeof node.title === 'string' ? node.title : '';
      const type = String(node.type || '').toUpperCase();
      const target = typeof node.url === 'string' ? node.url
        : (typeof node.slug === 'string' ? node.slug : '');
      if ((type === 'DOC' || !type) && target) out.push({ path: parents, url: target });
      const children = Array.isArray(node.children) ? node.children
        : (Array.isArray(node.nodes) ? node.nodes : []);
      walk(children, title ? parents.concat([title]) : parents);
    }
  };
  walk(toc, []);
  return JSON.stringify(out);
})()
"""


def _catalog_key(value: str | None) -> str | None:
    """把目录项/文档链接收敛成可比较的键：末段路径，去掉查询与锚点。"""
    if not value:
        return None
    path = urlparse(value).path or value
    segments = [segment for segment in path.split("/") if segment]
    return segments[-1] if segments else None


class RepositoryPage(BasePage):
    _document_selectors = (
        "[data-testid^='doc-']",
        "role=link[name=文档]",
        "testid=document-link",
        "text=文档",
    )

    async def list_documents(self, repository_id: str) -> list[RemoteDocument]:
        if hasattr(self.page, "bring_to_front"):
            return await self._list_from_catalog(repository_id)
        locator = await self.wait_for_any(self._document_selectors)
        locators = await maybe_await(locator.all())
        documents: list[RemoteDocument] = []
        for index, item in enumerate(locators, start=1):
            link = item.locator("a").first if hasattr(item, "locator") else item
            title = (await link.inner_text()).strip() or (await item.inner_text()).strip()
            if not title:
                continue
            url = await link.get_attribute("href") or await item.get_attribute("href")
            documents.append(
                RemoteDocument(
                    remote_id=url or f"document-{index}",
                    repository_id=repository_id,
                    title=title,
                    url=url,
                )
            )
        return documents

    async def _list_from_catalog(self, repository_id: str) -> list[RemoteDocument]:
        locator = self.page.locator(f"a[href*='{repository_id}/']")
        await locator.first.wait_for(state="visible", timeout=5_000)
        paths = await self.list_catalog_paths()
        documents: list[RemoteDocument] = []
        seen: set[str] = set()
        for item in await maybe_await(locator.all()):
            title = (await item.inner_text()).split("\n")[0].strip()
            if not title:
                continue
            url = await item.get_attribute("href")
            key = url or title
            if key in seen:
                continue
            seen.add(key)
            documents.append(
                RemoteDocument(
                    remote_id=url or title,
                    repository_id=repository_id,
                    title=title,
                    url=url,
                    path=paths.get(_catalog_key(url) or "", []),
                )
            )
        return documents

    async def list_catalog_paths(self) -> dict[str, list[str]]:
        """文档键（链接末段）→ 目录路径。任何意外一律当作"没有目录"。"""
        if not hasattr(self.page, "evaluate"):
            # WebDriver 的 locator 门面没有 evaluate；只有 Playwright 侧的页面有。
            # 这是可选信息，读不到就让界面平铺，不影响列出文档。
            return {}
        try:
            raw = await self.page.evaluate(_CATALOG_SCRIPT)
        except Exception:  # noqa: BLE001 - 目录是可选信息，失败不该连累文档列表
            return {}
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            entries = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(entries, list):
            return {}
        paths: dict[str, list[str]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = _catalog_key(entry.get("url"))
            if key is None:
                continue
            raw_path = entry.get("path")
            paths[key] = [str(part) for part in raw_path] if isinstance(raw_path, list) else []
        return paths

    async def open_new_document(self) -> None:
        await self.click_any(("role=button[name=新建文档]", "testid=create-document", "text=新建文档"))

    async def delete_current_document(self) -> None:
        await self.click_any(("role=button[name=删除]", "testid=delete-document", "text=删除"))
        await self.click_any(("role=button[name=确认删除]", "testid=confirm-delete", "text=确认"))
        await self.wait_for_any(("role=status[name=已删除]", "testid=delete-confirmation", "text=已删除"))
