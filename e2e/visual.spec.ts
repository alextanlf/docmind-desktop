import { completeFakeOnboarding, importFixture } from "./helpers";
import type { Locator, Page } from "@playwright/test";
import { expect, test } from "./fixtures/backend-fixture";

for (const viewport of [
  { width: 1440, height: 900, name: "1440x900", reference: "grid" },
  { width: 1280, height: 800, name: "1280x800", reference: "grid" },
  { width: 960, height: 640, name: "960x640", reference: "drawer" },
] as const) {
  test(`keeps workspace controls inside ${viewport.name}`, async ({
    electronApp,
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await expectFullyInViewport(page.getByRole("dialog", { name: "开始使用 DocMind" }));
    await completeFakeOnboarding(page);
    await importFixture(electronApp, page, "e2e/fixtures/state-guide.md");
    await expect(page.getByLabel("主导航")).toBeVisible();
    await expectFullyInViewport(page.getByLabel("发送消息"));
    await expectFullyInViewport(page.getByLabel("导入文档"));
    await expectPhysicalReferenceLayout(page, viewport.reference);
    await expect
      .poll(() =>
        page
          .locator("*")
          .evaluateAll((elements) =>
            elements.some((element) => element.scrollWidth > element.clientWidth),
          ),
      )
      .toBe(false);
    await page.screenshot({
      path: testInfo.outputPath(`${viewport.name}-workspace.png`),
      fullPage: true,
    });
    await page.getByLabel("导入文档").click();
    await expectFullyInViewport(page.getByRole("dialog"));
  });
}

async function expectFullyInViewport(locator: Locator) {
  await expect
    .poll(() =>
      locator.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return (
          bounds.top >= 0 &&
          bounds.left >= 0 &&
          bounds.bottom <= window.innerHeight &&
          bounds.right <= window.innerWidth
        );
      }),
    )
    .toBe(true);
}

async function expectPhysicalReferenceLayout(page: Page, reference: "grid" | "drawer") {
  await expect
    .poll(() =>
      page.locator(".workspace-reference").evaluate((referencePanel) => {
        const referenceBounds = referencePanel.getBoundingClientRect();
        const mainBounds = document.querySelector(".workspace-main")?.getBoundingClientRect();
        const position = getComputedStyle(referencePanel).position;
        return {
          isDrawer:
            position === "fixed" &&
            referenceBounds.right === window.innerWidth &&
            referenceBounds.width === 320,
          isGrid:
            position !== "fixed" &&
            mainBounds !== undefined &&
            referenceBounds.left >= mainBounds.right &&
            referenceBounds.width === 320,
        };
      }),
    )
    .toEqual(
      reference === "drawer"
        ? { isDrawer: true, isGrid: false }
        : { isDrawer: false, isGrid: true },
    );
}
