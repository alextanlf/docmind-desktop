from __future__ import annotations

from app.api.errors import DomainError
from app.schemas.yuque import YuqueRepository
from app.yuque.base_page import BasePage, maybe_await


class DashboardPage(BasePage):
    _repository_selectors = (
        "[data-testid^='books-']",
        "role=link[name=知识库]",
        "testid=repository-link",
        "text=知识库",
    )

    async def list_repositories(self) -> list[YuqueRepository]:
        if hasattr(self.page, "bring_to_front"):
            await self.page.goto("https://www.yuque.com/dashboard/books", wait_until="domcontentloaded")
            return await self._list_from_book_links()
        locator = await self.wait_for_any(self._repository_selectors)
        locators = await maybe_await(locator.all())
        repositories: list[YuqueRepository] = []
        for index, item in enumerate(locators, start=1):
            link = item.locator("a").first if hasattr(item, "locator") else item
            name = (await link.inner_text()).strip() or (await item.inner_text()).strip()
            if not name:
                continue
            url = await link.get_attribute("href") or await item.get_attribute("href")
            repositories.append(YuqueRepository(yuque_id=url or f"repository-{index}", name=name, url=url))
        return repositories

    async def _list_from_book_links(self) -> list[YuqueRepository]:
        repositories: list[YuqueRepository] = []
        seen: set[str] = set()
        locator = await self.wait_for_any(("a.book-link", "a.index-module_link_W1ONb"))
        for item in await maybe_await(locator.all()):
            name = (await item.inner_text()).strip()
            if not name:
                continue
            url = await item.get_attribute("href")
            key = url or name
            if key in seen:
                continue
            seen.add(key)
            repositories.append(YuqueRepository(yuque_id=url or name, name=name, url=url))
        return repositories

    async def create_repository(self, name: str) -> YuqueRepository:
        await self.submit_new_repository(name)
        return await self.find_repository(name)

    async def submit_new_repository(self, name: str) -> None:
        await self.click_any(("role=button[name=新建知识库]", "testid=create-repository", "text=新建知识库"))
        await self.fill_any(("role=textbox[name=知识库名称]", "testid=repository-name", "text=知识库名称"), name)
        await self.click_any(("role=button[name=创建]", "testid=create-repository-submit", "text=创建"))

    async def find_repository(self, name: str) -> YuqueRepository:
        repositories = await self.list_repositories()
        for repository in repositories:
            if repository.name == name:
                return repository
        raise DomainError("YUQUE_PAGE_CHANGED", "新建知识库后未找到知识库，请重新登录后重试", 503, True)
