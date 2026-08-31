from __future__ import annotations

from app.api.errors import DomainError
from app.schemas.yuque import YuqueRepository
from app.yuque.base_page import BasePage, maybe_await


class DashboardPage(BasePage):
    _repository_selectors = (
        "role=link[name=知识库]",
        "testid=repository-link",
        "text=知识库",
    )

    async def list_repositories(self) -> list[YuqueRepository]:
        locator = await self.wait_for_any(self._repository_selectors)
        locators = await maybe_await(locator.all())
        repositories: list[YuqueRepository] = []
        for index, item in enumerate(locators, start=1):
            name = (await item.inner_text()).strip()
            if not name:
                continue
            url = await item.get_attribute("href")
            repositories.append(YuqueRepository(yuque_id=url or f"repository-{index}", name=name, url=url))
        return repositories

    async def create_repository(self, name: str) -> YuqueRepository:
        await self.click_any(("role=button[name=新建知识库]", "testid=create-repository", "text=新建知识库"))
        await self.fill_any(("role=textbox[name=知识库名称]", "testid=repository-name", "text=知识库名称"), name)
        await self.click_any(("role=button[name=创建]", "testid=create-repository-submit", "text=创建"))
        repositories = await self.list_repositories()
        for repository in repositories:
            if repository.name == name:
                return repository
        raise DomainError("YUQUE_PAGE_CHANGED", "新建知识库后未找到知识库，请重新登录后重试", 503, True)
