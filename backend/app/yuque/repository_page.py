from __future__ import annotations

from app.schemas.yuque import YuqueDocument
from app.yuque.base_page import BasePage, maybe_await


class RepositoryPage(BasePage):
    _document_selectors = (
        "role=link[name=文档]",
        "testid=document-link",
        "text=文档",
    )

    async def list_documents(self, repository_id: str) -> list[YuqueDocument]:
        locator = await self.wait_for_any(self._document_selectors)
        locators = await maybe_await(locator.all())
        documents: list[YuqueDocument] = []
        for index, item in enumerate(locators, start=1):
            title = (await item.inner_text()).strip()
            if not title:
                continue
            url = await item.get_attribute("href")
            documents.append(
                YuqueDocument(
                    yuque_id=url or f"document-{index}",
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
