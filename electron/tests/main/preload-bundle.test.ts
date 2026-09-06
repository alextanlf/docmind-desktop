import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

it("executes the production preload using only the sandbox Electron module", async () => {
  const root = resolve(__dirname, "../../..");
  await promisify(execFile)(
    process.execPath,
    [resolve(root, "node_modules/electron-vite/bin/electron-vite.js"), "build"],
    { cwd: root, env: process.env },
  );
  const exposeInMainWorld = vi.fn();
  const source = await readFile(resolve(root, "out/preload/index.js"), "utf8");
  runInNewContext(source, {
    require: (name: string) => {
      if (name !== "electron") throw new Error(`Sandbox cannot load ${name}`);
      return {
        contextBridge: { exposeInMainWorld },
        ipcRenderer: { on: vi.fn(), invoke: vi.fn(), send: vi.fn(), removeListener: vi.fn() },
      };
    },
  });
  expect(exposeInMainWorld).toHaveBeenCalledWith(
    "docmind",
    expect.objectContaining({
      settings: expect.any(Object),
      batches: expect.any(Object),
      memory: expect.any(Object),
    }),
  );
}, 30_000);
