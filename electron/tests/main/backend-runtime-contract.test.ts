import { describe, expect, it } from "vitest";
import { mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { resolveBackendRuntime } from "../../main/backend-runtime-contract";

describe("backend runtime contract", () => {
  it("resolves fixed development command", () => {
    expect(
      resolveBackendRuntime({}, { packaged: false, repoDir: "/repo", dataDir: "/data" }),
    ).toEqual({
      command: "uv",
      args: ["run", "python", "-m", "app"],
      cwd: "/repo/backend",
      dataDir: "/data",
      port: 18900,
    });
  });
  it("accepts an explicit executable packaged command", () => {
    const root = mkdtempSync(join(tmpdir(), "docmind-runtime-"));
    const command = join(root, "python");
    const cwd = join(root, "backend");
    writeFileSync(command, "#!/bin/sh\n");
    chmodSync(command, 0o755);
    mkdirSync(cwd);
    expect(
      resolveBackendRuntime(
        {
          DOCMIND_BACKEND_COMMAND: command,
          DOCMIND_BACKEND_ARGS: '["-m","app"]',
          DOCMIND_BACKEND_CWD: cwd,
        },
        { packaged: true, repoDir: "/repo", dataDir: "/data" },
      ).args,
    ).toEqual(["-m", "app"]);
  });
  it("rejects invalid packaged contracts", () => {
    expect(() =>
      resolveBackendRuntime(
        { DOCMIND_BACKEND_COMMAND: "relative" },
        { packaged: true, repoDir: "/repo", dataDir: "/data" },
      ),
    ).toThrow("BACKEND_START_FAILED");
  });
  it("requires a JSON args array and an actual backend directory", () => {
    const root = mkdtempSync(join(tmpdir(), "docmind-runtime-invalid-"));
    const command = join(root, "python");
    writeFileSync(command, "#!/bin/sh\n");
    chmodSync(command, 0o755);
    expect(() =>
      resolveBackendRuntime(
        { DOCMIND_BACKEND_COMMAND: command, DOCMIND_BACKEND_CWD: root },
        { packaged: true, repoDir: "/repo", dataDir: "/data" },
      ),
    ).toThrow("BACKEND_START_FAILED");
    expect(() =>
      resolveBackendRuntime(
        {
          DOCMIND_BACKEND_COMMAND: command,
          DOCMIND_BACKEND_ARGS: "[]",
          DOCMIND_BACKEND_CWD: command,
        },
        { packaged: true, repoDir: "/repo", dataDir: "/data" },
      ),
    ).toThrow("BACKEND_START_FAILED");
  });

  // Info.plist 只能存固定字符串，写不了绝对路径，因此打包态用 $RESOURCES 占位符，
  // 由主进程用 process.resourcesPath 展开。少了这一步包会退回构建机路径而失效。
  it("expands $RESOURCES placeholders against resourcesPath", () => {
    const resources = mkdtempSync(join(tmpdir(), "docmind-resources-"));
    const runtimeDir = join(resources, "backend-runtime");
    const command = join(runtimeDir, "bin", "python3");
    mkdirSync(join(runtimeDir, "bin"), { recursive: true });
    writeFileSync(command, "#!/bin/sh\n");
    chmodSync(command, 0o755);
    const resolved = resolveBackendRuntime(
      {
        DOCMIND_BACKEND_COMMAND: "$RESOURCES/backend-runtime/bin/python3",
        DOCMIND_BACKEND_ARGS: '["run.py"]',
        DOCMIND_BACKEND_CWD: "$RESOURCES/backend-runtime",
      },
      { packaged: true, repoDir: "/repo", dataDir: "/data", resourcesPath: resources },
    );
    expect(resolved.command).toBe(command);
    expect(resolved.cwd).toBe(runtimeDir);
    expect(resolved.args).toEqual(["run.py"]);
  });
  it("rejects $RESOURCES placeholders when resourcesPath is absent", () => {
    const resources = mkdtempSync(join(tmpdir(), "docmind-resources-missing-"));
    const runtimeDir = join(resources, "backend-runtime");
    mkdirSync(join(runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(runtimeDir, "bin", "python3"), "#!/bin/sh\n");
    chmodSync(join(runtimeDir, "bin", "python3"), 0o755);
    expect(() =>
      resolveBackendRuntime(
        {
          DOCMIND_BACKEND_COMMAND: "$RESOURCES/backend-runtime/bin/python3",
          DOCMIND_BACKEND_ARGS: '["run.py"]',
          DOCMIND_BACKEND_CWD: "$RESOURCES/backend-runtime",
        },
        //开发态没有 resourcesPath，占位符无法解析 —— 必须报错而不是静默用相对路径
        { packaged: true, repoDir: "/repo", dataDir: "/data" },
      ),
    ).toThrow("BACKEND_START_FAILED");
  });
  it("keeps non-placeholder absolute paths untouched", () => {
    const root = mkdtempSync(join(tmpdir(), "docmind-runtime-abs-"));
    const command = join(root, "python");
    const cwd = join(root, "backend");
    writeFileSync(command, "#!/bin/sh\n");
    chmodSync(command, 0o755);
    mkdirSync(cwd);
    const resolved = resolveBackendRuntime(
      {
        DOCMIND_BACKEND_COMMAND: command,
        DOCMIND_BACKEND_ARGS: '["-m","app"]',
        DOCMIND_BACKEND_CWD: cwd,
      },
      {
        packaged: true,
        repoDir: "/repo",
        dataDir: "/data",
        resourcesPath: "/should/not/be/used",
      },
    );
    expect(resolved.command).toBe(command);
    expect(resolved.cwd).toBe(cwd);
  });

  // Windows 的 NSIS 安装包没有 LSEnvironment 这种 env 注入机制，main 进程只能
  // 自己从 process.resourcesPath 推导出后端入口。下面两条守住这条兜底路径。
  describe("packaged runtime derived from resourcesPath", () => {
    function resourcesWith(relativeExecutable: string) {
      const resources = mkdtempSync(join(tmpdir(), "docmind-bundled-"));
      const runtimeDir = join(resources, "backend-runtime");
      const command = join(runtimeDir, relativeExecutable);
      mkdirSync(dirname(command), { recursive: true });
      writeFileSync(command, "#!/bin/sh\n");
      chmodSync(command, 0o755);
      return { resources, runtimeDir, command };
    }

    it("finds the POSIX bin/python3 layout without any env contract", () => {
      const { resources, runtimeDir, command } = resourcesWith(join("bin", "python3"));
      const resolved = resolveBackendRuntime(
        {},
        {
          packaged: true,
          repoDir: "/repo",
          dataDir: "/data",
          resourcesPath: resources,
          platform: "darwin",
        },
      );
      expect(resolved.command).toBe(command);
      expect(resolved.cwd).toBe(runtimeDir);
      expect(resolved.args).toEqual(["run.py"]);
    });

    it("finds the Windows python.exe layout next to Lib/", () => {
      const { resources, runtimeDir, command } = resourcesWith("python.exe");
      const resolved = resolveBackendRuntime(
        {},
        {
          packaged: true,
          repoDir: "/repo",
          dataDir: "/data",
          resourcesPath: resources,
          platform: "win32",
        },
      );
      expect(resolved.command).toBe(command);
      expect(resolved.cwd).toBe(runtimeDir);
      expect(resolved.args).toEqual(["run.py"]);
    });

    it("does not mistake a Windows package for a POSIX one", () => {
      // 只存在 bin/python3 时，win32 不该命中 —— 两个平台的候选列表是分开的，
      // 否则会出现「在 mac 上构建的 runtime 被 Windows 认领」的假阳性。
      const { resources } = resourcesWith(join("bin", "python3"));
      expect(() =>
        resolveBackendRuntime(
          {},
          { packaged: true, repoDir: "/repo", dataDir: "/data", resourcesPath: resources, platform: "win32" },
        ),
      ).toThrow("BACKEND_START_FAILED");
    });

    it("prefers an explicit env contract over the derived one", () => {
      const { resources } = resourcesWith("python.exe");
      const root = mkdtempSync(join(tmpdir(), "docmind-explicit-"));
      const command = join(root, "python");
      const cwd = join(root, "backend");
      writeFileSync(command, "#!/bin/sh\n");
      chmodSync(command, 0o755);
      mkdirSync(cwd);
      const resolved = resolveBackendRuntime(
        {
          DOCMIND_BACKEND_COMMAND: command,
          DOCMIND_BACKEND_ARGS: '["-m","app"]',
          DOCMIND_BACKEND_CWD: cwd,
        },
        { packaged: true, repoDir: "/repo", dataDir: "/data", resourcesPath: resources, platform: "win32" },
      );
      expect(resolved.command).toBe(command);
      expect(resolved.args).toEqual(["-m", "app"]);
    });

    it("falls back to the derived runtime when the env contract is unusable", () => {
      // 构建机路径被写死进 Info.plist（历史遗留）时，env 契约会指向不存在的文件。
      // 此时应回退到 resourcesPath 自解析，而不是让整个应用起不来。
      const { resources, runtimeDir } = resourcesWith("python.exe");
      const resolved = resolveBackendRuntime(
        {
          DOCMIND_BACKEND_COMMAND: "/nonexistent/python3",
          DOCMIND_BACKEND_ARGS: '["run.py"]',
          DOCMIND_BACKEND_CWD: "/nonexistent/runtime",
        },
        { packaged: true, repoDir: "/repo", dataDir: "/data", resourcesPath: resources, platform: "win32" },
      );
      expect(resolved.cwd).toBe(runtimeDir);
    });

    it("fails when the runtime is absent from the bundle", () => {
      const resources = mkdtempSync(join(tmpdir(), "docmind-empty-bundle-"));
      expect(() =>
        resolveBackendRuntime(
          {},
          { packaged: true, repoDir: "/repo", dataDir: "/data", resourcesPath: resources, platform: "win32" },
        ),
      ).toThrow("BACKEND_START_FAILED");
    });
  });
});
