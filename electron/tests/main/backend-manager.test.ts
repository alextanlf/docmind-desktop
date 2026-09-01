import { describe, expect, it, vi } from "vitest";
import { BackendManager } from "../../main/backend-manager";

function fakeProcess() {
  const listeners: Record<string, (...args: unknown[]) => void> = {};
  return {
    pid: 42,
    stderr: {
      on: vi.fn(
        (_: string, cb: (data: string) => void) =>
          (listeners.stderr = cb as any),
      ),
    },
    on: vi.fn(
      (e: string, cb: (...a: unknown[]) => void) => (listeners[e] = cb),
    ),
    kill: vi.fn(),
    once: vi.fn(
      (e: string, cb: (...a: unknown[]) => void) => (listeners[e] = cb),
    ),
    __exit: () => listeners.exit?.(1),
    __error: (e: Error) => listeners.error?.(e),
    __stderr: (s: string) => listeners.stderr?.(s),
  };
}

describe("BackendManager", () => {
  it("passes a random runtime token and waits for authenticated health", async () => {
    const process = fakeProcess();
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response('{"status":"ok","version":"0.1.0"}'));
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch,
      dataDir: "/tmp/docmind",
      healthIntervalMs: 0,
    });
    const connection = await manager.start();
    expect(connection.token).toMatch(/^[a-f0-9]{64}$/);
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:18900/health",
      expect.objectContaining({
        headers: { "X-DocMind-Token": connection.token },
      }),
    );
  });

  it("uses backend working directory in development to resolve app module", async () => {
    const process = fakeProcess();
    const spawn = vi.fn(() => process as any);
    const manager = new BackendManager({
      spawn,
      fetch: vi.fn().mockResolvedValue(new Response("{}")),
      dataDir: "/tmp/docmind",
      repoDir: "/repo",
      healthIntervalMs: 0,
    });
    await manager.start();
    expect(spawn).toHaveBeenCalledWith(
      "uv",
      ["run", "python", "-m", "app"],
      expect.objectContaining({ cwd: "/repo/backend" }),
    );
  });
  it("fails fast when packaged backend command is not explicitly configured", async () => {
    const manager = new BackendManager({
      packaged: true,
      dataDir: "/tmp/docmind",
    });
    await expect(manager.start()).rejects.toMatchObject({
      code: "BACKEND_START_FAILED",
    });
  });
  it("starts packaged backend only with configured command arguments and cwd", async () => {
    const process = fakeProcess();
    const spawn = vi.fn(() => process as any);
    const manager = new BackendManager({
      spawn,
      fetch: vi.fn().mockResolvedValue(new Response("{}")),
      packaged: true,
      backendCommand: "/bundle/python",
      backendArgs: ["-m", "app"],
      backendCwd: "/bundle/backend",
      healthIntervalMs: 0,
    });
    await manager.start();
    expect(spawn).toHaveBeenCalledWith(
      "/bundle/python",
      ["-m", "app"],
      expect.objectContaining({ cwd: "/bundle/backend" }),
    );
  });

  it.each([[[1]], [[null]], [[{ value: "-m" }]]])(
    "rejects packaged backend arguments containing non-strings (%j) before spawning",
    async (invalidArgs) => {
      const process = fakeProcess();
      const spawn = vi.fn(() => process as any);
      const manager = new BackendManager({
        spawn,
        fetch: vi.fn(),
        packaged: true,
        backendCommand: "/bundle/python",
        backendArgs: invalidArgs as any,
        backendCwd: "/bundle/backend",
        healthIntervalMs: 0,
        startupTimeoutMs: 10,
      });

      await expect(manager.start()).rejects.toMatchObject({
        code: "BACKEND_START_FAILED",
      });
      expect(spawn).not.toHaveBeenCalled();
    },
  );

  it("throws start failure when child exits", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: vi.fn().mockRejectedValue(new Error("down")),
      healthIntervalMs: 0,
      startupTimeoutMs: 20,
    });
    setTimeout(() => process.__exit(), 1);
    await expect(manager.start()).rejects.toMatchObject({
      code: "BACKEND_START_FAILED",
    });
  });

  it("terminates exact child on stop", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: vi.fn().mockResolvedValue(new Response("{}")),
      healthIntervalMs: 0,
      shutdownTimeoutMs: 0,
    });
    await manager.start();
    await manager.stop();
    expect(process.kill).toHaveBeenNthCalledWith(1, "SIGTERM");
    expect(process.kill).toHaveBeenNthCalledWith(2, "SIGKILL");
  });

  it("redacts raw runtime token and paths from child diagnostics", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: vi.fn().mockRejectedValue(new Error("down")),
      dataDir: "/tmp/private",
      healthIntervalMs: 0,
      startupTimeoutMs: 20,
    });
    const pending = manager.start();
    process.__stderr(
      "failed token DOCMIND_SESSION_TOKEN=will-be-redacted at /tmp/private/log",
    );
    process.__exit();
    const error = (await pending.catch((value) => value as Error)) as Error;
    expect(error.message).not.toContain("/tmp/private");
    expect(error.message).not.toMatch(/[a-f0-9]{64}/);
  });

  it("maps child process errors to sanitized start failure", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: vi.fn(),
      healthIntervalMs: 0,
      shutdownTimeoutMs: 0,
    });
    const pending = manager.start();
    process.__error(new Error("spawn /private/path failed"));
    await expect(pending).rejects.toMatchObject({
      code: "BACKEND_START_FAILED",
    });
  });

  it("includes stderr emitted after a child error in sanitized diagnostics", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: () => new Promise<Response>(() => {}),
      healthIntervalMs: 0,
      shutdownTimeoutMs: 0,
    });
    const pending = manager.start();
    process.__error(new Error("spawn failed"));
    process.__stderr("late startup failure at /private/backend/log");

    const error = (await pending.catch((value) => value as Error)) as Error;
    expect(error).toMatchObject({ code: "BACKEND_START_FAILED" });
    expect(error.message).toContain("late startup failure");
    expect(error.message).not.toContain("/private/backend/log");
  }, 100);

  it("rejects promptly when child errors during a hanging health request", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: () => new Promise<Response>(() => {}),
      healthIntervalMs: 0,
      shutdownTimeoutMs: 0,
    });
    const pending = manager.start();
    process.__error(new Error("spawn failed"));
    await expect(pending).rejects.toMatchObject({
      code: "BACKEND_START_FAILED",
    });
  }, 100);

  it("cleans up timed-out child before a retry can spawn another backend", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: vi.fn().mockRejectedValue(new Error("down")),
      healthIntervalMs: 0,
      startupTimeoutMs: 1,
      shutdownTimeoutMs: 0,
    });
    await expect(manager.start()).rejects.toMatchObject({
      code: "BACKEND_START_FAILED",
    });
    expect(process.kill).toHaveBeenCalledWith("SIGTERM");
  });
});
