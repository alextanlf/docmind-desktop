import { describe, expect, it, vi } from "vitest";
import { registerIpcHandlers, serializeIpcError } from "../../main/ipc-handlers";

function dependencies() {
  return {
    proxy: {
      requestJson: vi.fn().mockResolvedValue({}),
      requestVoid: vi.fn().mockResolvedValue(undefined),
      openStream: vi.fn().mockReturnValue({
        requestId: "00000000-0000-0000-0000-000000000001",
        cancel: vi.fn(),
      }),
      cancel: vi.fn(),
      cleanup: vi.fn(),
    },
    stagedFiles: { chooseAndStage: vi.fn(), stageDirectory: vi.fn() },
    shellOpenExternal: vi.fn(),
    ipcMain: {
      handle: vi.fn(),
      on: vi.fn(),
      removeHandler: vi.fn(),
      removeAllListeners: vi.fn(),
    },
  } as any;
}

describe("IPC handlers", () => {
  it("never exposes arbitrary backend requests", () => {
    const handlers = registerIpcHandlers(dependencies());
    expect(Object.keys(handlers)).not.toContain("backend:request");
    expect(Object.keys(handlers)).toContain("settings:get");
    expect(Object.keys(handlers)).toContain("imports:create");
  });

  it("validates UUID path parameters before interpolation", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);
    expect(() => handlers["documents:read"]({} as any, "not-a-uuid")).toThrow(/请求参数无效/);
    expect(deps.proxy.requestJson).not.toHaveBeenCalled();
  });

  it("cleans up stream listeners on renderer destruction", () => {
    const deps = dependencies();
    const destroyed = { on: vi.fn() };
    registerIpcHandlers({ ...deps, getWebContents: () => destroyed } as any);
    expect(destroyed.on).toHaveBeenCalledWith("destroyed", expect.any(Function));
  });

  it("resolves ipcMain handlers with a cloneable stable error envelope", async () => {
    const deps = dependencies();
    const registered = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
    deps.ipcMain.handle.mockImplementation((channel: string, handler: any) =>
      registered.set(channel, handler),
    );
    deps.proxy.requestJson.mockRejectedValue(new Error("failed at /private/secret/token"));
    registerIpcHandlers(deps);
    const wrapped = registered.get("settings:get");
    const result = await wrapped?.({});
    const cloned = JSON.parse(JSON.stringify(result));
    expect(cloned).toEqual({
      ok: false,
      error: {
        code: "BACKEND_REQUEST_FAILED",
        message: "请求失败",
        retryable: false,
      },
    });
    expect(JSON.stringify(cloned)).not.toMatch(/private|failed/);
  });

  it("resolves successful ipcMain handlers with a cloneable value envelope", async () => {
    const deps = dependencies();
    const registered = new Map<string, any>();
    deps.ipcMain.handle.mockImplementation((channel: string, handler: any) =>
      registered.set(channel, handler),
    );
    deps.proxy.requestJson.mockResolvedValue({ status: "ok" });
    registerIpcHandlers(deps);
    await expect(registered.get("settings:get")({})).resolves.toEqual({
      ok: true,
      value: { status: "ok" },
    });
  });

  it("sanitizes staged source preview paths at the typed boundary", async () => {
    const deps = dependencies();
    deps.proxy.requestJson.mockResolvedValue({
      title: "指南",
      sourceKind: "staged_file",
      sourceUrl: "/private/user/imports/staging/secret.md",
      mediaType: "text/markdown",
      sizeBytes: 1,
      fingerprint: "a".repeat(64),
      warnings: [],
    });
    const handlers = registerIpcHandlers(deps);
    const result = await handlers["imports:inspect"](
      {},
      { kind: "staged_file", value: "opaque-id" },
    );
    expect(result.sourceUrl).toBeNull();
  });

  it("emits a terminal serializable stream error when stream startup throws", () => {
    const deps = dependencies();
    const sender = { send: vi.fn() };
    deps.proxy.openStream.mockImplementation(() => {
      throw new Error("duplicate /private/path");
    });
    const listeners = new Map<string, any>();
    deps.ipcMain.on.mockImplementation((channel: string, handler: any) =>
      listeners.set(channel, handler),
    );
    registerIpcHandlers(deps);
    listeners.get("imports:subscribe")({ sender }, "00000000-0000-0000-0000-000000000001", 0);
    expect(sender.send).toHaveBeenCalledWith(
      "stream:event:00000000-0000-0000-0000-000000000001",
      expect.objectContaining({
        type: "error",
        sequence: 0,
        payload: expect.objectContaining({
          code: "BACKEND_REQUEST_FAILED",
          message: "请求失败",
        }),
      }),
    );
  });

  it("maps staged file errors to Simplified Chinese without exposing source details", () => {
    expect(
      serializeIpcError({
        code: "SOURCE_TOO_LARGE",
        message: "Source exceeds size limit",
      }),
    ).toMatchObject({
      code: "SOURCE_TOO_LARGE",
      message: "文件超过大小限制",
    });
  });

  it("stages a directory only through its fixed zero-argument IPC channel", async () => {
    const deps = dependencies();
    deps.stagedFiles.stageDirectory.mockResolvedValue({
      collectionId: "00000000-0000-0000-0000-000000000021",
      displayName: "docs",
      itemCount: 2,
      totalBytes: 12,
      selectedPath: "/private/user/docs",
    });
    const handlers = registerIpcHandlers(deps);

    await expect(handlers["sources:stageDirectory"]({})).resolves.toEqual({
      collectionId: "00000000-0000-0000-0000-000000000021",
      displayName: "docs",
      itemCount: 2,
      totalBytes: 12,
    });
    expect(JSON.stringify(await handlers["sources:stageDirectory"]({}))).not.toContain("/private");
    expect(() => handlers["sources:stageDirectory"]({}, "/attacker/path")).toThrow(/请求参数无效/);
  });

  it("maps directory staging failures without leaking a selected path", () => {
    expect(
      serializeIpcError({
        code: "BATCH_SOURCE_CHANGED",
        message: "hash failed at /private/user/docs/secret.md",
      }),
    ).toEqual({
      code: "BATCH_SOURCE_CHANGED",
      message: "目录内容在暂存期间发生变化",
      retryable: false,
    });
    expect(
      serializeIpcError({
        code: "BATCH_LIMIT_EXCEEDED",
        message: "1001 files under /private/user/docs",
      }),
    ).toEqual({
      code: "BATCH_LIMIT_EXCEEDED",
      message: "目录超过批量导入限制",
      retryable: false,
    });
  });
});
