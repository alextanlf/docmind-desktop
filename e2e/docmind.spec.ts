import {
  completeFakeOnboarding,
  createChatSession,
  expandRepository,
  importFixture,
} from "./helpers";
import { expect, test } from "./fixtures/backend-fixture";

test("onboards, imports Markdown and answers with a citation", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await expect(page.getByText("导入完成")).toBeVisible({ timeout: 15000 });
  await createChatSession(page);
  await page.getByLabel("输入问题").fill("@State 有什么作用？");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "查看引用 S1" })).toBeVisible();
  await page.getByRole("button", { name: "查看引用 S1" }).click();
  await expect(page.getByRole("complementary", { name: "引用资料", exact: true })).toContainText(
    "@State",
  );
  await electronApp.expectHealthy();
});

test("shows stable settings authentication failure copy", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await electronApp.failNextModelTest();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("API Key 无效，请更新密钥后重试");
});

test("allows an import to be cancelled", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await electronApp.delayNextImport();
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md", {
    waitForCompletion: false,
  });
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
});

test("retries an indexing failure", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await electronApp.failNextIndex();
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md", {
    waitForCompletion: false,
  });
  await expect(page.getByRole("button", { name: "重试导入" })).toBeVisible();
  await page.getByRole("button", { name: "重试导入" }).click();
  await expect(page.getByText("导入完成")).toBeVisible();
});

test("requires a duplicate-content decision", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md", {
    stopAtDuplicateDecision: true,
  });
  await expect(page.getByLabel("重复内容处理")).toBeVisible();
  await page.getByLabel("重复内容处理").selectOption("update");
  await page.getByRole("button", { name: "继续" }).click();
  await page.getByRole("button", { name: "确认导入" }).click();
  await expect(page.getByText("导入完成")).toBeVisible();
});

test("archives a document without a title confirmation and restores it from settings", async ({
  electronApp,
  page,
}) => {
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await expandRepository(page);
  await page.getByRole("button", { name: "状态管理", exact: true }).click();
  await page.getByLabel("归档文档").click();
  // 归档可撤销，所以这里**没有**"输入标题以确认"那道门。
  await expect(page.getByLabel("输入文档标题以确认")).toHaveCount(0);
  await page.getByRole("button", { name: "归档", exact: true }).click();
  await expect(page.getByRole("button", { name: "状态管理", exact: true })).toHaveCount(0);

  // 恢复入口在设置页：归档之后文档不在任何列表里，撤销入口不能跟着内容一起消失。
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "已归档的文档" }).click();
  await expect(page.getByRole("list", { name: "已归档的文档" })).toContainText("状态管理");
  await page.getByRole("button", { name: "恢复 状态管理" }).click();
  await expect(page.getByRole("list", { name: "已归档的文档" })).toHaveCount(0);
});

test("archives selected documents and points at the restore entry", async ({
  electronApp,
  page,
}) => {
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await expect(page.getByText("导入完成")).toBeVisible();
  await expandRepository(page);

  await page.getByLabel("选择 状态管理").check();
  await page.getByRole("button", { name: "归档所选的 1 篇文档" }).click();
  // 文案要说清"东西去哪了、怎么回来"：只说"已归档"会让用户以为内容被删了。
  await expect(page.getByText(/设置 →\s*已归档的文档/)).toBeVisible();
  await page.getByRole("button", { name: "归档", exact: true }).click();

  await expect(page.getByRole("button", { name: "状态管理", exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "已归档的文档" }).click();
  await expect(page.getByRole("list", { name: "已归档的文档" })).toContainText("状态管理");
});

test("removes a knowledge base only after name confirmation", async ({ electronApp, page }) => {
  // 这条走的是导入时新建的**本地**知识库（e2e 的假服务不预置远端仓库），
  // 所以断言的是本地语义：没有撤销入口，也就不该出现「已移除的知识库」。
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await expect(page.getByText("导入完成")).toBeVisible();
  await expandRepository(page);

  await page.getByLabel("移除 SwiftUI").click();
  const confirm = page.getByRole("button", { name: "移除知识库" });
  // 名字不匹配时按钮必须点不动 —— 误点一次就是丢一整个知识库。
  await expect(confirm).toBeDisabled();
  await page.getByLabel("输入知识库名称以确认").fill("别的名字");
  await expect(confirm).toBeDisabled();
  await page.getByLabel("输入知识库名称以确认").fill("SwiftUI");
  await expect(confirm).toBeEnabled();
  await confirm.click();

  await expect(page.getByRole("button", { name: "展开 SwiftUI" })).toHaveCount(0);
  await expect(page.getByText("已移除的知识库")).toHaveCount(0);
  // 本地库被删掉之后，它的文档也不该还能在树里点开。
  await expect(page.getByRole("button", { name: "状态管理", exact: true })).toHaveCount(0);
});

test("persists fake-service data across an app restart", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  const restartedPage = await electronApp.restart();
  await expect(restartedPage.getByLabel("工作台")).toBeVisible();
  await expandRepository(restartedPage);
  await expect(restartedPage.getByRole("button", { name: "状态管理", exact: true })).toBeVisible();
});

test("shuts down the backend gracefully", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await electronApp.close();
  await expect.poll(() => electronApp.backendExited()).toBe(true);
});
