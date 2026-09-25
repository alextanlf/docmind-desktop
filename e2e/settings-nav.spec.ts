import { completeFakeOnboarding } from "./helpers";
import { expect, test } from "./fixtures/backend-fixture";

for (const viewport of [
  { width: 1280, height: 800, name: "1280x800" },
  { width: 960, height: 640, name: "960x640" },
] as const) {
  test(`keeps settings sections reachable at ${viewport.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await completeFakeOnboarding(page);
    await page.getByLabel("设置", { exact: true }).click();

    const nav = page.getByRole("navigation", { name: "设置分区" });
    await expect(nav).toBeVisible();
    await expect(nav.getByRole("button", { name: "对话模型" })).toHaveAttribute(
      "aria-current",
      "true",
    );

    await nav.getByRole("button", { name: "本地数据与诊断" }).click();
    await expect(nav.getByRole("button", { name: "本地数据与诊断" })).toHaveAttribute(
      "aria-current",
      "true",
    );
    await expect(page.getByRole("heading", { name: "本地数据与诊断" })).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-settings.png`) });
  });
}
