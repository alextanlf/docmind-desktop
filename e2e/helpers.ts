import { expect, type Page } from "@playwright/test";
import { resolve } from "node:path";
import type { FixtureDialog } from "./fixtures/backend-fixture";

type ImportOptions = {
  stopAtDuplicateDecision?: boolean;
  waitForCompletion?: boolean;
};

export async function completeFakeOnboarding(page: Page): Promise<void> {
  await page.getByLabel("模型预设").selectOption("deepseek");
  await page.getByLabel("API Key").fill("fake-key");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByRole("button", { name: "测试模型连接" }).click();
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "打开语雀登录" }).click();
  await expect(page.getByText("已登录", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByLabel("工作台")).toBeVisible();
}

export async function importFixture(
  dialog: FixtureDialog,
  page: Page,
  fixture: string,
  options: ImportOptions = {},
): Promise<void> {
  const fixturePath = resolve(process.cwd(), fixture);
  await dialog.setDialogFixture(fixture);
  await page.getByLabel("导入文档").click();
  await page.getByRole("button", { name: "Markdown" }).click();
  await page.getByRole("button", { name: "选择 Markdown 文件" }).click();
  await expect(page.getByText(fixturePath.split("/").pop() ?? "state-guide.md")).toBeVisible();
  await page.getByRole("button", { name: "继续" }).click();
  const repository = page.getByLabel("目标知识库");
  const hasSwiftUI = (await repository.locator("option").allTextContents()).includes("SwiftUI");
  if (hasSwiftUI) {
    await repository.selectOption({ label: "SwiftUI" });
  } else {
    await page.getByLabel("新建知识库名称").fill("SwiftUI");
    await page.getByRole("button", { name: "新建知识库" }).click();
  }
  if (options.stopAtDuplicateDecision) return;
  await page.getByRole("button", { name: "继续" }).click();
  await expect(page.getByText("准备 Embedding 模型"))
    .toBeVisible({ timeout: 15000 })
    .catch(() => undefined);
  const prepareModel = page.getByRole("button", { name: "准备模型" });
  if (await prepareModel.isVisible().catch(() => false)) {
    await prepareModel.click();
  }
  await expect(prepareModel).toHaveCount(0, { timeout: 15000 });
  const confirm = page.getByRole("button", { name: "确认导入" });
  await expect(confirm).toBeEnabled({ timeout: 15000 });
  await confirm.click();
  await expect(page.getByRole("region", { name: "导入进度", exact: true })).toBeVisible();
  if (options.waitForCompletion !== false) await expect(page.getByText("导入完成")).toBeVisible();
}

export async function createChatSession(page: Page, repositoryName = "SwiftUI"): Promise<void> {
  await page.getByLabel("选择知识库").click();
  await page.getByRole("checkbox", { name: repositoryName }).check();
  await page.getByRole("button", { name: "新建会话" }).click();
  await expect(page.getByLabel("输入问题")).toBeEnabled();
}

export async function expandRepository(page: Page, repositoryName = "SwiftUI"): Promise<void> {
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15000 });
  await page.keyboard.press("Escape").catch(() => undefined);
  const expand = page
    .locator('button.repository-tree-button[aria-expanded="false"]')
    .filter({ hasText: repositoryName });
  const collapse = page
    .locator('button.repository-tree-button[aria-expanded="true"]')
    .filter({ hasText: repositoryName });
  if (await collapse.isVisible().catch(() => false)) return;
  await expect(expand).toBeVisible({ timeout: 15000 });
  await expand.click();
  await expect(collapse).toBeVisible({ timeout: 15000 });
}
