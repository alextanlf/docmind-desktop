from __future__ import annotations

from app.yuque.base_page import BasePage


class EditorPage(BasePage):
    async def import_markdown(self, content: str) -> None:
        await self.fill_any(("role=textbox[name=Markdown]", "testid=editor-markdown", "text=Markdown"), content)
        await self.click_any(("role=button[name=保存]", "testid=editor-save", "text=保存"))
        await self.wait_for_any(("role=status[name=已保存]", "testid=save-confirmation", "text=已保存"))

    async def set_title(self, title: str) -> None:
        await self.fill_any(("role=textbox[name=标题]", "testid=editor-title", "text=标题"), title)

    async def save(self) -> None:
        """只保存当前表单，**不碰正文**。

        与 ``import_markdown`` 分开是为了改名：那个方法先填 Markdown 再保存，而改名
        不该把正文再提交一次（正文一旦被重新写入，用户没碰过的内容就可能被转换规则
        改掉）。这里只按保存。
        """
        await self.click_any(("role=button[name=保存]", "testid=editor-save", "text=保存"))
        await self.wait_for_any(("role=status[name=已保存]", "testid=save-confirmation", "text=已保存"))

    async def read_markdown(self) -> str:
        locator = await self.wait_for_any(("testid=editor-markdown", "[data-testid=editor-markdown]"))
        return await locator.input_value()

    async def read_title(self) -> str:
        locator = await self.wait_for_any(("testid=editor-title", "[data-testid=editor-title]"))
        return (await locator.input_value()).strip()
