import { describe, expect, it, vi } from "vitest";
import { BackendManager } from "../../main/backend-manager";

function fakeProcess() {
  const listeners: Record<string, (...args: unknown[]) => void> = {};
  return {
    pid: 42,
    stderr: {
      on: vi.fn((_: string, cb: (data: string) => void) => (listeners.stderr = cb as any)),
    },
    on: vi.fn((e: string, cb: (...a: unknown[]) => void) => (listeners[e] = cb)),
    kill: vi.fn(),
    once: vi.fn((e: string, cb: (...a: unknown[]) => void) => (listeners[e] = cb)),
    __exit: () => listeners.exit?.(1),
    __error: (e: Error) => listeners.error?.(e),
    __stderr: (s: string) => listeners.stderr?.(s),
  };
}

describe("BackendManager", () => {
  it("passes a random runtime token and waits for authenticated health", async () => {
    const process = fakeProcess();
    const fetch = vi.fn().mockResolvedValue(new Response('{"status":"ok","version":"0.1.0"}'));
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

  it("serializes concurrent starts so only one backend is spawned", async () => {
    const process = fakeProcess();
    const spawn = vi.fn(() => process as any);
    let resolveHealth!: (response: Response) => void;
    let markHealthRequested!: () => void;
    const healthRequested = new Promise<void>((resolve) => {
      markHealthRequested = resolve;
    });
    const healthResponse = new Promise<Response>((resolve) => {
      resolveHealth = resolve;
    });
    const fetch = vi.fn(() => {
      markHealthRequested();
      return healthResponse;
    });
    const manager = new BackendManager({
      spawn,
      fetch: fetch as typeof globalThis.fetch,
      healthIntervalMs: 0,
    });

    const first = manager.start();
    const second = manager.start();
    await healthRequested;
    resolveHealth(new Response("{}"));
    const [firstConnection, secondConnection] = await Promise.all([first, second]);

    expect(spawn).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(secondConnection).toEqual(firstConnection);
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

  it("rejects sparse packaged backend arguments before spawning", async () => {
    const process = fakeProcess();
    const spawn = vi.fn(() => process as any);
    const sparseArgs = Array<string>(2);
    sparseArgs[1] = "app";
    const manager = new BackendManager({
      spawn,
      fetch: vi.fn(),
      packaged: true,
      backendCommand: "/bundle/python",
      backendArgs: sparseArgs,
      backendCwd: "/bundle/backend",
      startupTimeoutMs: 1,
      shutdownTimeoutMs: 0,
    });

    await expect(manager.start()).rejects.toMatchObject({
      code: "BACKEND_START_FAILED",
    });
    expect(spawn).not.toHaveBeenCalled();
  });

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

  it("acknowledges shutdown only after the backend child exits", async () => {
    const process = fakeProcess();
    process.kill.mockImplementation((signal: string) => {
      if (signal === "SIGTERM") process.__exit();
    });
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: vi.fn().mockResolvedValue(new Response("{}")),
      healthIntervalMs: 0,
      shutdownTimeoutMs: 10,
    });
    await manager.start();

    await expect(manager.stop()).resolves.toBe(true);
  });

  it("serializes concurrent stops on the same child exit", async () => {
    const process = fakeProcess();
    const manager = new BackendManager({
      spawn: () => process as any,
      fetch: vi.fn().mockResolvedValue(new Response("{}")),
      healthIntervalMs: 0,
      shutdownTimeoutMs: 100,
    });
    await manager.start();

    const first = manager.stop();
    const second = manager.stop();
    let secondSettled = false;
    void second.then(() => {
      secondSettled = true;
    });
    await Promise.resolve();
    const settledBeforeExit = secondSettled;
    process.__exit();
    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);

    expect(settledBeforeExit).toBe(false);
    expect(process.kill).toHaveBeenCalledTimes(1);
    expect(process.kill).toHaveBeenCalledWith("SIGTERM");
  });

  it("waits for shutdown before a concurrent restart spawns", async () => {
    const firstProcess = fakeProcess();
    const secondProcess = fakeProcess();
    const processes = [firstProcess, secondProcess];
    const spawn = vi.fn(() => processes.shift() as any);
    const manager = new BackendManager({
      spawn,
      fetch: vi.fn().mockResolvedValue(new Response("{}")),
      healthIntervalMs: 0,
      shutdownTimeoutMs: 100,
    });
    await manager.start();

    const stopping = manager.stop();
    const restarting = manager.start();
    await Promise.resolve();
    const spawnCountBeforeExit = spawn.mock.calls.length;
    firstProcess.__exit();
    await stopping;
    await restarting;

    expect(spawnCountBeforeExit).toBe(1);
    expect(spawn).toHaveBeenCalledTimes(2);
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
    process.__stderr("failed token DOCMIND_SESSION_TOKEN=will-be-redacted at /tmp/private/log");
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

  it("aborts a hanging health probe at the overall startup deadline", async () => {
    vi.useFakeTimers();
    try {
      const process = fakeProcess();
      process.kill.mockImplementation(() => process.__exit());
      let rejectProbe: (error: Error) => void = () => {};
      const fetch = vi.fn((_: string | URL, init?: RequestInit) => {
        const signal = init?.signal;
        return new Promise<Response>((_, reject) => {
          rejectProbe = reject;
          signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        });
      });
      const manager = new BackendManager({
        spawn: () => process as any,
        fetch: fetch as typeof globalThis.fetch,
        healthIntervalMs: 0,
        startupTimeoutMs: 50,
      });

      const outcome = manager.start().then(
        () => undefined,
        (error: unknown) => error,
      );
      const probeSignal = (fetch.mock.calls[0]?.[1] as RequestInit | undefined)?.signal ?? null;
      if (probeSignal === null) {
        process.__exit();
        rejectProbe(new Error("test cleanup"));
        await vi.runAllTimersAsync();
        await outcome;
      }

      expect(probeSignal).not.toBeNull();
      await vi.advanceTimersByTimeAsync(50);
      await expect(outcome).resolves.toMatchObject({ code: "BACKEND_START_FAILED" });
      expect(probeSignal?.aborted).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

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
