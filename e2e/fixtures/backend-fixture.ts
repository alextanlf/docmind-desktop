import { expect as baseExpect, test as base, type Page } from "@playwright/test";
import { _electron as electron, type ElectronApplication } from "playwright";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";

const root = resolve(__dirname, "../..");
const fixturePath = resolve(root, "e2e/fixtures/state-guide.md");

type ElectronHarness = {
  app: ElectronApplication;
  page: Page;
  close(): Promise<void>;
  restart(): Promise<void>;
  backendExited(): Promise<boolean>;
  expectHealthy(): Promise<void>;
  failNextModelTest(): Promise<void>;
  delayNextImport(): Promise<void>;
  failNextIndex(): Promise<void>;
};

type Fixtures = { electronApp: ElectronHarness; page: Page };

export const test = base.extend<Fixtures>({
  electronApp: async (_fixtures, use, testInfo) => {
    const dataDir = await mkdtemp(join(tmpdir(), "docmind-e2e-"));
    const artifacts = testInfo.outputPath("backend");
    await mkdir(artifacts, { recursive: true });
    let app = await launch(dataDir, artifacts);
    let page = await app.firstWindow();
    const consoleErrors: string[] = [];
    const attachPageGuards = (window: Page) => {
      window.on("console", (message) => {
        if (message.type() === "error") consoleErrors.push(message.text());
      });
      window.on("pageerror", (error) => consoleErrors.push(error.message));
    };
    attachPageGuards(page);
    const harness: ElectronHarness = {
      get app() {
        return app;
      },
      get page() {
        return page;
      },
      async close() {
        await app.close();
      },
      async restart() {
        await app.close();
        app = await launch(dataDir, artifacts);
        page = await app.firstWindow();
        attachPageGuards(page);
      },
      async backendExited() {
        const log = await readFile(join(artifacts, "backend.log"), "utf8").catch(() => "");
        return log.includes("backend exited") || app.process().exitCode !== null;
      },
      async expectHealthy() {
        expect(consoleErrors, "renderer console/page errors").toEqual([]);
      },
      async failNextModelTest() {
        await writeControl(dataDir, "fail-next-model-test");
      },
      async delayNextImport() {
        await writeControl(dataDir, "delay-next-import");
      },
      async failNextIndex() {
        await writeControl(dataDir, "fail-next-index");
      },
    };
    try {
      await use(harness);
      expect(consoleErrors, "renderer console/page errors").toEqual([]);
    } catch (error) {
      await page
        .screenshot({ path: testInfo.outputPath("failure.png"), fullPage: true })
        .catch(() => {});
      await page
        .context()
        .tracing.stop({ path: testInfo.outputPath("trace.zip") })
        .catch(() => {});
      throw error;
    } finally {
      await app.close().catch(() => {});
      await rm(dataDir, { force: true, recursive: true });
    }
  },
  page: async ({ electronApp }, use) => {
    await use(electronApp.page);
  },
});

export const expect = baseExpect;

async function launch(dataDir: string, artifacts: string): Promise<ElectronApplication> {
  const app = await electron.launch({
    args: [resolve(root, "out/main/index.js")],
    env: {
      ...process.env,
      DOCMIND_E2E: "1",
      DOCMIND_E2E_DATA_DIR: dataDir,
      DOCMIND_E2E_DIALOG_PATH: fixturePath,
      DOCMIND_E2E_ARTIFACTS_DIR: artifacts,
      DOCMIND_FAKE_SERVICES: "1",
    },
  });
  await app.context().tracing.start({ screenshots: true, snapshots: true, sources: true });
  return app;
}

async function writeControl(dataDir: string, command: string): Promise<void> {
  const control = join(dataDir, "e2e", "control");
  await mkdir(join(dataDir, "e2e"), { recursive: true });
  await writeFile(control, command, "utf8");
}
