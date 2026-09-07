import { access } from "node:fs/promises";
import { afterEach, describe, expect, it } from "vitest";
import {
  createTemporaryRuntimeDataDir,
  recordLocalRuntimeSmoke,
  resolveElectronExecutable,
} from "../../main/local-runtime-smoke";

const cleanups: Array<() => Promise<void>> = [];

afterEach(async () => {
  await Promise.all(cleanups.splice(0).map((cleanup) => cleanup()));
});

describe("local runtime smoke record", () => {
  it("creates and cleans a temporary data directory", async () => {
    const runtime = await createTemporaryRuntimeDataDir();
    cleanups.push(runtime.cleanup);
    expect(runtime.kind).toBe("temporary");
    expect(runtime.path).not.toBe(process.env.HOME);
    await access(runtime.path);
    await runtime.cleanup();
    await expect(access(runtime.path)).rejects.toThrow();
  });

  it("records only the four lifecycle outcomes", () => {
    const record = recordLocalRuntimeSmoke({
      mode: "dev",
      health: "PASS",
      rendererLoaded: "PASS",
      backendExited: "PASS",
      portReleased: "PASS",
    });
    expect(record).toEqual({
      mode: "dev",
      dataDirKind: "temporary",
      health: "PASS",
      rendererLoaded: "PASS",
      backendExited: "PASS",
      portReleased: "PASS",
    });
    expect(JSON.stringify(record)).not.toMatch(/DOCMIND_SESSION_TOKEN|\/Users\//);
  });

  it("requires an explicit installed Electron executable instead of downloading one", () => {
    expect(() => resolveElectronExecutable({}, () => false)).toThrow("ELECTRON_RUNTIME_REQUIRED");
    expect(() =>
      resolveElectronExecutable({ DOCMIND_ELECTRON_PATH: "/missing/Electron" }, () => false),
    ).toThrow("ELECTRON_RUNTIME_REQUIRED");
    expect(
      resolveElectronExecutable(
        { DOCMIND_ELECTRON_PATH: "/Applications/Electron.app/Contents/MacOS/Electron" },
        () => true,
      ),
    ).toContain("Electron.app");
  });
});
