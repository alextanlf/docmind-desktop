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
  await expect(page.getByText("已连接语雀")).toBeVisible();
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
    await page.getByRole("button", { name: "创建知识库" }).click();
  }
  await page.getByRole("button", { name: "继续" }).click();
  await page.getByRole("button", { name: "准备模型" }).click();
  await page.getByRole("button", { name: "确认导入" }).click();
  await expect(page.getByLabel("导入进度")).toBeVisible();
  if (options.stopAtDuplicateDecision) return;
  if (options.waitForCompletion !== false) await expect(page.getByText("导入完成")).toBeVisible();
}
