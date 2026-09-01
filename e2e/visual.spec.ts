import { completeFakeOnboarding, importFixture } from "./helpers";
import { expect, test } from "./fixtures/backend-fixture";

for (const viewport of [
  { width: 1440, height: 900, name: "1440x900", reference: "grid" },
  { width: 1280, height: 800, name: "1280x800", reference: "grid" },
  { width: 960, height: 640, name: "960x640", reference: "drawer" },
] as const) {
  test(`keeps workspace controls inside ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await completeFakeOnboarding(page);
    await importFixture(page, "e2e/fixtures/state-guide.md");
    await expect(page.getByLabel("主导航")).toBeVisible();
    await expect(page.getByLabel("发送消息")).toBeInViewport();
    await expect(page.getByLabel("导入文档")).toBeInViewport();
    await expect(page.locator("[aria-label='工作台']")).toHaveAttribute(
      "data-reference-layout",
      viewport.reference,
    );
    await expect
      .poll(() =>
        page
          .locator("*")
          .evaluateAll((elements) =>
            elements.some((element) => element.scrollWidth > element.clientWidth),
          ),
      )
      .toBe(false);
    await expect(page).toHaveScreenshot(`${viewport.name}-workspace.png`, {
      fullPage: true,
      animations: "disabled",
    });
    await page.getByLabel("导入文档").click();
    await expect(page.getByRole("dialog")).toBeInViewport();
  });
}
