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

test("removes a document only after title confirmation", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await expandRepository(page);
  await page.getByRole("button", { name: "状态管理", exact: true }).click();
  await page.getByLabel("移除文档").click();
  await page.getByLabel("输入文档标题以确认").fill("状态管理");
  await page.getByRole("button", { name: "移除文档" }).click();
  await expect(page.getByRole("button", { name: "状态管理", exact: true })).toHaveCount(0);
});

test("removes selected documents and warns that local ones cannot come back", async ({
  electronApp,
  page,
}) => {
  // 走的是导入新建的本地知识库：本地文档没有「恢复」这条路，所以对话框必须单独
  // 说明"无法恢复"，而不是和可恢复的远端文档混成一个总数。
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await expect(page.getByText("导入完成")).toBeVisible();
  await expandRepository(page);

  await page.getByLabel("选择 状态管理").check();
  await page.getByRole("button", { name: "移除所选的 1 篇文档" }).click();
  await expect(page.getByText(/1 篇是本地文档，移除后无法恢复/)).toBeVisible();
  await page.getByRole("button", { name: "移除 1 篇" }).click();

  await expect(page.getByRole("button", { name: "状态管理", exact: true })).toHaveCount(0);
  // 本地文档不产生「已移除的文档」条目 —— 那条路只对远端身份有意义。
  await expect(page.getByText("已移除的文档")).toHaveCount(0);
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
