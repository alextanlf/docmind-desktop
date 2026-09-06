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
  await page.getByLabel("设置").click();
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

test("deletes a remote document only after title confirmation", async ({ electronApp, page }) => {
  await completeFakeOnboarding(page);
  await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
  await expandRepository(page);
  await page.getByRole("button", { name: "状态管理", exact: true }).click();
  await page.getByLabel("删除文档").click();
  await page.getByLabel("输入文档标题以确认").fill("状态管理");
  await page.getByRole("button", { name: "删除文档" }).click();
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
