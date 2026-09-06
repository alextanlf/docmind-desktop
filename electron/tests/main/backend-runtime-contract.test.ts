import { describe, expect, it } from "vitest";
import { mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveBackendRuntime } from "../../main/backend-runtime-contract";

describe("backend runtime contract", () => {
  it("resolves fixed development command", () => {
    expect(resolveBackendRuntime({}, { packaged: false, repoDir: "/repo", dataDir: "/data" })).toEqual({ command: "uv", args: ["run", "python", "-m", "app"], cwd: "/repo/backend", dataDir: "/data", port: 18900 });
  });
  it("accepts an explicit executable packaged command", () => {
    const root = mkdtempSync(join(tmpdir(), "docmind-runtime-"));
    const command = join(root, "python"); const cwd = join(root, "backend");
    writeFileSync(command, "#!/bin/sh\n"); chmodSync(command, 0o755); mkdirSync(cwd);
    expect(resolveBackendRuntime({ DOCMIND_BACKEND_COMMAND: command, DOCMIND_BACKEND_ARGS: '["-m","app"]', DOCMIND_BACKEND_CWD: cwd }, { packaged: true, repoDir: "/repo", dataDir: "/data" }).args).toEqual(["-m", "app"]);
  });
  it("rejects invalid packaged contracts", () => {
    expect(() => resolveBackendRuntime({ DOCMIND_BACKEND_COMMAND: "relative" }, { packaged: true, repoDir: "/repo", dataDir: "/data" })).toThrow("BACKEND_START_FAILED");
  });
});
