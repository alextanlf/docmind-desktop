import { expect as baseExpect, test as base, type Page } from "@playwright/test";
import type { Electron, ElectronApplication } from "playwright";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import {
  recordLocalRuntimeSmoke,
  resolveElectronExecutable,
} from "../../electron/main/local-runtime-smoke";

const root = resolve(__dirname, "../..");
export type FixtureDialog = {
  setDialogFixture(fixture: string): Promise<void>;
};

type ElectronHarness = FixtureDialog & {
  app: ElectronApplication;
  page: Page;
  close(): Promise<void>;
  restart(): Promise<Page>;
  backendExited(): Promise<boolean>;
  expectHealthy(): Promise<void>;
  failNextModelTest(): Promise<void>;
  delayNextImport(): Promise<void>;
  failNextIndex(): Promise<void>;
  runLocalRuntimeSmoke(): Promise<ReturnType<typeof recordLocalRuntimeSmoke>>;
};

type Fixtures = { electronApp: ElectronHarness; page: Page };

export const test = base.extend<Fixtures>({
  electronApp: async ({ playwright }, use, testInfo) => {
    const dataDir = await mkdtemp(join(tmpdir(), "docmind-e2e-"));
    const artifacts = testInfo.outputPath("backend");
    await mkdir(artifacts, { recursive: true });
    let app = await launch(playwright._electron, dataDir, artifacts);
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
        const before =
          (await readFile(join(artifacts, "backend.log"), "utf8").catch(() => "")).match(
            /backend exited/g,
          )?.length ?? 0;
        await app.close();
        await expect
          .poll(
            async () =>
              (await readFile(join(artifacts, "backend.log"), "utf8").catch(() => "")).match(
                /backend exited/g,
              )?.length ?? 0,
            { timeout: 5000 },
          )
          .toBeGreaterThan(before);
        app = await launch(playwright._electron, dataDir, artifacts);
        page = await app.firstWindow();
        attachPageGuards(page);
        return page;
      },
      async backendExited() {
        const log = await readFile(join(artifacts, "backend.log"), "utf8").catch(() => "");
        return log.includes("backend exited");
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
      async runLocalRuntimeSmoke() {
        const rendererLoaded = await page
          .waitForLoadState("load", { timeout: 10_000 })
          .then(() => page.evaluate(() => document.readyState === "complete"))
          .then((loaded) => (loaded ? "PASS" : ("FAIL" as const)))
          .catch(() => "FAIL" as const);
        const health = rendererLoaded === "PASS" ? "PASS" : "FAIL";
        await app.close().catch(() => {});
        const backendExited = await waitForBackendExit(artifacts);
        const portReleased = backendExited === "PASS" ? await waitForPortRelease(18900) : "NOT RUN";
        return recordLocalRuntimeSmoke({
          mode: "dev",
          health,
          rendererLoaded,
          backendExited,
          portReleased,
        });
      },
      async setDialogFixture(fixture) {
        await writeDialogFixture(dataDir, resolve(root, fixture));
      },
    };
    try {
      await use(harness);
      expect(consoleErrors, "renderer console/page errors").toEqual([]);
    } catch (error) {
      const consoleArtifact = testInfo.outputPath("console-errors.txt");
      await writeFile(consoleArtifact, consoleErrors.join("\n"), "utf8").catch(() => {});
      await testInfo.attach("console-errors", { path: consoleArtifact, contentType: "text/plain" });
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

async function launch(
  electron: Electron,
  dataDir: string,
  artifacts: string,
): Promise<ElectronApplication> {
  const app = await electron.launch({
    executablePath: resolveElectronExecutable(process.env),
    // CI/headless macOS runners may not expose a usable GPU process. Keep the
    // product's normal GPU path unchanged and make only the fake E2E harness
    // deterministic.
    args: [
      "--disable-gpu",
      "--in-process-gpu",
      "--disable-software-rasterizer",
      "--no-sandbox",
      resolve(root, "out/main/index.js"),
    ],
    env: {
      ...process.env,
      DOCMIND_E2E: "1",
      DOCMIND_E2E_DATA_DIR: dataDir,
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

async function writeDialogFixture(dataDir: string, fixture: string): Promise<void> {
  const control = join(dataDir, "e2e", "dialog-path");
  await mkdir(join(dataDir, "e2e"), { recursive: true });
  await writeFile(control, fixture, "utf8");
}

async function waitForBackendExit(artifacts: string): Promise<"PASS" | "FAIL"> {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const log = await readFile(join(artifacts, "backend.log"), "utf8").catch(() => "");
    if (log.includes("backend exited")) return "PASS";
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 50));
  }
  return "FAIL";
}

async function waitForPortRelease(port: number): Promise<"PASS" | "FAIL"> {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const released = await new Promise<boolean>((resolveConnection) => {
      const socket = net.createConnection({ host: "127.0.0.1", port });
      socket.once("connect", () => {
        socket.destroy();
        resolveConnection(false);
      });
      socket.once("error", (error: NodeJS.ErrnoException) => {
        socket.destroy();
        resolveConnection(error.code === "ECONNREFUSED");
      });
    });
    if (released) return "PASS";
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 50));
  }
  return "FAIL";
}
