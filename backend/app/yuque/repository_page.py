from __future__ import annotations

from app.schemas.yuque import YuqueDocument
from app.yuque.base_page import BasePage, maybe_await


class RepositoryPage(BasePage):
    _document_selectors = (
        "[data-testid^='doc-']",
        "role=link[name=文档]",
        "testid=document-link",
        "text=文档",
    )

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        if hasattr(self.page, "bring_to_front"):
            return await self._list_from_catalog(repository_id)
        locator = await self.wait_for_any(self._document_selectors)
        locators = await maybe_await(locator.all())
        documents: list[YuqueDocument] = []
        for index, item in enumerate(locators, start=1):
            link = item.locator("a").first if hasattr(item, "locator") else item
            title = (await link.inner_text()).strip() or (await item.inner_text()).strip()
            if not title:
                continue
            url = await link.get_attribute("href") or await item.get_attribute("href")
            documents.append(
                YuqueDocument(
                    yuque_id=url or f"document-{index}",
                    repository_id=repository_id,
                    title=title,
                    url=url,
                )
            )
        return documents

    async def _list_from_catalog(self, repository_id: str) -> list[YuqueDocument]:
        locator = self.page.locator(f"a[href*='{repository_id}/']")
        await locator.first.wait_for(state="visible", timeout=5_000)
        documents: list[YuqueDocument] = []
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
                YuqueDocument(
                    yuque_id=url or title,
                    repository_id=repository_id,
                    title=title,
                    url=url,
                )
            )
        return documents

    async def open_new_document(self) -> None:
        await self.click_any(("role=button[name=新建文档]", "testid=create-document", "text=新建文档"))

    async def delete_current_document(self) -> None:
        await self.click_any(("role=button[name=删除]", "testid=delete-document", "text=删除"))
        await self.click_any(("role=button[name=确认删除]", "testid=confirm-delete", "text=确认"))
        await self.wait_for_any(("role=status[name=已删除]", "testid=delete-confirmation", "text=已删除"))
