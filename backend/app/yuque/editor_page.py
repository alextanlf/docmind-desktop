from __future__ import annotations

from app.yuque.base_page import BasePage


class EditorPage(BasePage):
    async def import_markdown(self, content: str) -> None:
        await self.fill_any(("role=textbox[name=Markdown]", "testid=editor-markdown", "text=Markdown"), content)
        await self.click_any(("role=button[name=保存]", "testid=editor-save", "text=保存"))
        await self.wait_for_any(("role=status[name=已保存]", "testid=save-confirmation", "text=已保存"))

    async def set_title(self, title: str) -> None:
        await self.fill_any(("role=textbox[name=标题]", "testid=editor-title", "text=标题"), title)

    async def read_markdown(self) -> str:
        locator = await self.wait_for_any(("testid=editor-markdown", "[data-testid=editor-markdown]"))
        return await locator.input_value()
