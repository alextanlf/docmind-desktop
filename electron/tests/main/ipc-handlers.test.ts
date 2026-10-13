import { describe, expect, it, vi } from "vitest";
import { FILE_DOMAIN, pathToken } from "../../main/path-tokens";
import { streamEventChannel } from "../../shared/channels";
import { registerIpcHandlers, serializeIpcError } from "../../main/ipc-handlers";

function dependencies() {
  return {
    proxy: {
      requestJson: vi.fn().mockResolvedValue({}),
      requestVoid: vi.fn().mockResolvedValue(undefined),
      requestRange: vi.fn().mockResolvedValue({ data: new Uint8Array([1, 2, 3]), total: 3 }),
      openStream: vi.fn().mockReturnValue({
        requestId: "00000000-0000-0000-0000-000000000001",
        cancel: vi.fn(),
      }),
      cancel: vi.fn(),
      cleanup: vi.fn(),
    },
    stagedFiles: {
      cancelScan: vi.fn(),
      chooseAndLink: vi.fn(),
      chooseAndLinkAny: vi.fn(),
      scanDirectory: vi.fn(),
      signGrantedPath: vi.fn(),
    },
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

  it("routes model profile actions to their own backend endpoints", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);

    await handlers["settings:deleteModel"]({} as any, "relay01");
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/settings/model/profiles/relay01",
      expect.objectContaining({ method: "DELETE" }),
      expect.anything(),
    );

    await handlers["settings:activateModel"]({} as any, "relay01");
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/settings/model/activate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ profileId: "relay01" }),
      }),
      expect.anything(),
    );

    // 测的是表单草稿：预设必须跟着走，否则后端只会去测当前使用的那一条。
    await handlers["settings:testModel"]({} as any, { preset: "kimi", model: "kimi-k3" });
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/settings/model/test",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ preset: "kimi", model: "kimi-k3" }),
      }),
      expect.anything(),
    );
  });

  it("rejects malformed model profile ids before interpolation", () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);
    // 档案 id 要进 URL 路径，不能让调用方塞进 `../` 之类的东西。
    expect(() => handlers["settings:deleteModel"]({} as any, "../admin")).toThrow(/请求参数无效/);
    expect(() => handlers["settings:activateModel"]({} as any, "NOPE")).toThrow(/请求参数无效/);
    expect(deps.proxy.requestJson).not.toHaveBeenCalled();
  });

  it("forwards original document ranges with base64 payloads", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);

    const chunk = await handlers["documents:readOriginalChunk"](
      {} as any,
      "00000000-0000-0000-0000-0000000000aa",
      0,
      2,
    );

    expect(deps.proxy.requestRange).toHaveBeenCalledWith(
      "/api/documents/00000000-0000-0000-0000-0000000000aa/raw",
      0,
      2,
    );
    // base64 而非 TypedArray：contextBridge 不保证透传二进制。
    expect(chunk).toEqual({ data: "AQID", total: 3 });
  });

  it("rejects nonsensical original ranges before touching the backend", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);
    const id = "00000000-0000-0000-0000-0000000000aa";
    const requestRange = (documentId: unknown, begin: unknown, end: unknown) =>
      handlers["documents:readOriginalChunk"]({} as any, documentId, begin, end);

    await expect(requestRange("not-a-uuid", 0, 1)).rejects.toThrow(/请求参数无效/);
    await expect(requestRange(id, -1, 1)).rejects.toThrow(/请求参数无效/);
    await expect(requestRange(id, 0, 1.5)).rejects.toThrow(/请求参数无效/);
    await expect(requestRange(id, "0", 1)).rejects.toThrow(/请求参数无效/);
    // begin 大于 end 不能被静默夹取，否则阅读器会拿到错位的数据。
    await expect(requestRange(id, 10, 4)).rejects.toThrow(/请求参数无效/);
    expect(deps.proxy.requestRange).not.toHaveBeenCalled();
  });

  it("proxies remote provider calls to the parameterized backend route", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);
    await handlers["remote:status"]({} as any, "yuque");
    expect(deps.proxy.requestJson).toHaveBeenCalledWith(
      "/api/remote/providers/yuque/status",
      {},
      expect.anything(),
    );
    await handlers["remote:login"]({} as any, "yuque");
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/remote/providers/yuque/login",
      expect.objectContaining({ method: "POST" }),
      expect.anything(),
    );
    await handlers["remote:installBrowser"]({} as any, "yuque");
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/remote/providers/yuque/browser/install",
      expect.objectContaining({ method: "POST" }),
      expect.anything(),
    );
  });

  it("rejects malformed remote provider names before interpolation", () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);
    expect(() => handlers["remote:status"]({} as any, "../admin")).toThrow(/请求参数无效/);
    expect(() => handlers["remote:login"]({} as any, "")).toThrow(/请求参数无效/);
    expect(deps.proxy.requestJson).not.toHaveBeenCalled();
  });

  it("proxies credential calls to the parameterized backend routes", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);
    await handlers["remote:listProviders"]({} as any);
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/remote/providers",
      {},
      expect.anything(),
    );
    await handlers["remote:listCredentials"]({} as any, "yuque");
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/remote/providers/yuque/credentials",
      {},
      expect.anything(),
    );
    await handlers["remote:saveCredential"]({} as any, "yuque", "api", { secret: "t" });
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/remote/providers/yuque/credentials/api",
      expect.objectContaining({ method: "PUT" }),
      expect.anything(),
    );
    await handlers["remote:testCredential"]({} as any, "yuque", "api");
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/remote/providers/yuque/credentials/api/test",
      expect.objectContaining({ method: "POST" }),
      expect.anything(),
    );
    await handlers["remote:deleteCredential"]({} as any, "yuque", "api");
    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      "/api/remote/providers/yuque/credentials/api",
      expect.objectContaining({ method: "DELETE" }),
      expect.anything(),
    );
    expect(() =>
      handlers["remote:saveCredential"]({} as any, "yuque", "../x", { secret: "t" }),
    ).toThrow(/请求参数无效/);
  });

  it("proxies repository parent updates through the repository route", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);
    const repositoryId = "00000000-0000-0000-0000-000000000021";

    await handlers["repositories:update"]({} as any, repositoryId, { remoteParentId: "wikParent" });

    expect(deps.proxy.requestJson).toHaveBeenLastCalledWith(
      `/api/repositories/${repositoryId}`,
      expect.objectContaining({ method: "PATCH" }),
      expect.anything(),
    );
    expect(() =>
      handlers["repositories:update"]({} as any, "not-a-uuid", { remoteParentId: null }),
    ).toThrow(/请求参数无效/);
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

  it("streams a directory scan back under the caller's own id", async () => {
    const deps = dependencies();
    const scanId = "00000000-0000-4000-8000-0000000000aa";
    let emit: ((event: unknown) => void) | null = null;
    // 假的扫描：把 emit 抓下来，由用例决定推进度还是推结果 —— 这样"边扫边报"是被测的时序，
    // 而不是被 mock 掉的实现细节。
    deps.stagedFiles.scanDirectory.mockImplementation(
      (_formats: unknown, id: string, onEvent: (event: unknown) => void) => {
        emit = onEvent;
        return Promise.resolve(id);
      },
    );
    const sent: { channel: string; payload: any }[] = [];
    const event = {
      sender: { send: (channel: string, payload: unknown) => sent.push({ channel, payload }) },
    };
    const handlers = registerIpcHandlers(deps);

    await expect(handlers["sources:scanDirectory"](event as never, scanId)).resolves.toBe(scanId);
    expect(emit).not.toBeNull();

    emit!({ type: "progress", scanned: 12, total: 300 });
    emit!({
      type: "done",
      collection: {
        collectionId: "00000000-0000-0000-0000-000000000021",
        displayName: "docs",
        itemCount: 2,
        totalBytes: 12,
      },
    });

    // **一个 id 走完全程**：扫描拿到的、事件流路由用的、取消时用的都是同一个。
    // 内部另生成一个 id 的话，"取消"会在另一张表里找不到东西，而只断言
    // "cancelScan 被调用过"的用例发现不了 —— 它确实被调用了，只是找不到。
    expect(deps.stagedFiles.scanDirectory).toHaveBeenCalledWith(
      expect.anything(),
      scanId,
      expect.any(Function),
    );
    // 事件都走在同一个 id 的流上 —— 渲染层订阅的就是它。
    expect(new Set(sent.map((entry) => entry.channel))).toEqual(
      new Set([streamEventChannel(scanId)]),
    );
    expect(sent.map((entry) => entry.payload.type)).toEqual(["progress", "done"]);
    expect(sent[0].payload.payload).toEqual({ scanned: 12, total: 300 });
    // 结果里只有清单元数据：用户选的绝对路径不出主进程。
    expect(JSON.stringify(sent[1].payload)).not.toContain("/private");
  });

  it("routes a scan cancel through the same id it started with", async () => {
    const deps = dependencies();
    deps.stagedFiles.scanDirectory.mockResolvedValue("scan-1");
    const handlers = registerIpcHandlers(deps);
    const scanId = "00000000-0000-4000-8000-0000000000aa";

    await handlers["sources:scanDirectory"]({ sender: { send: vi.fn() } } as never, scanId);
    handlers["stream:cancel"]({} as never, scanId);

    // 关键：取消用的 id 必须就是开扫时用的那一个。内部另生成一个 id 的话，
    // 这里会"调用了但取消失败"，而任何只断言"cancelScan 被调用过"的用例都发现不了。
    expect(deps.stagedFiles.cancelScan).toHaveBeenCalledWith(scanId);
  });

  it("rejects a malformed scan id before touching the backend", async () => {
    const deps = dependencies();
    const handlers = registerIpcHandlers(deps);

    await expect(
      handlers["sources:scanDirectory"]({ sender: { send: vi.fn() } } as never, "not-a-uuid"),
    ).rejects.toThrow(/请求参数无效/);
    expect(deps.proxy.requestJson).not.toHaveBeenCalled();
  });

  it("maps directory staging failures without leaking a selected path", () => {
    expect(
      serializeIpcError({
        code: "BATCH_SOURCE_CHANGED",
        message: "hash failed at /private/user/docs/secret.md",
      }),
    ).toEqual({
      code: "BATCH_SOURCE_CHANGED",
      message: "目录内容在扫描期间发生变化",
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

  it("relaunches before quitting so the backend stops before the new instance starts", async () => {
    const order: string[] = [];
    const deps = dependencies();
    deps.app = {
      relaunch: vi.fn(() => order.push("relaunch")),
      quit: vi.fn(() => order.push("quit")),
    };
    const handlers = registerIpcHandlers(deps);

    await handlers["app:restart"]({});

    // Order matters: relaunch() only takes effect on exit, and quit() routes
    // through before-quit where index.ts stops the backend. Reversing them would
    // leave the old runtime holding port 18900 against the new instance.
    expect(order).toEqual(["relaunch", "quit"]);
  });

  it("refuses to restart when no app handle is wired", () => {
    const handlers = registerIpcHandlers(dependencies());

    expect(() => handlers["app:restart"]({})).toThrowError(/无法重启应用/);
  });
});

/**
 * 链接型导入的签名发生在**主进程这一层**，所以它的性质也只能在这里测。
 *
 * 这里守的是"渲染层不能指名任意文件"：签名由主进程补，而主进程只给对话框登记过的
 * 路径签。渲染层编一个路径过来，要么被 `signGrantedPath` 拒掉，要么根本没有签名 ——
 * 两种情况下后端都读不到东西。
 */
describe("链接型导入的路径签名", () => {
  const LINKED = {
    kind: "linked_file",
    value: "/Users/someone/论文/attention.pdf",
    name: "attention.pdf",
  };

  /** `imports:inspect` 会把后端响应再校验一遍，所以假响应也必须是合法形状。 */
  const PREVIEW = {
    title: "attention.pdf",
    sourceKind: "linked_file",
    sourceUrl: null,
    mediaType: "application/pdf",
    sizeBytes: 12,
    fingerprint: "a".repeat(64),
    warnings: [],
  };

  it("carries the signed path into the backend request", async () => {
    const deps = dependencies();
    deps.proxy.requestJson.mockResolvedValue(PREVIEW);
    deps.stagedFiles.signGrantedPath = vi.fn().mockReturnValue("signed-token");
    const handlers = registerIpcHandlers(deps);

    await handlers["imports:inspect"]({} as any, LINKED);

    const body = JSON.parse((deps.proxy.requestJson as any).mock.calls.at(-1)[1].body as string);
    expect(body.pathToken).toBe("signed-token");
    expect(deps.stagedFiles.signGrantedPath).toHaveBeenCalledWith(LINKED.value);
  });

  it("refuses a path the dialog never granted", async () => {
    const deps = dependencies();
    // 真实的 signGrantedPath 对未登记的路径抛错；这里照原样模拟那个行为。
    deps.stagedFiles.signGrantedPath = vi.fn(() => {
      throw new Error("Path was not granted by a file dialog");
    });
    const handlers = registerIpcHandlers(deps);

    await expect(handlers["imports:inspect"]({} as any, LINKED)).rejects.toThrow();

    expect(deps.proxy.requestJson).not.toHaveBeenCalled();
  });

  it("signs on the create path too, not just on inspect", async () => {
    const deps = dependencies();
    deps.stagedFiles.signGrantedPath = vi.fn().mockReturnValue("signed-token");
    const handlers = registerIpcHandlers(deps);

    await handlers["imports:create"]({} as any, {
      source: LINKED,
      repositoryId: "00000000-0000-0000-0000-000000000001",
      fingerprint: "a".repeat(64),
    });

    const body = JSON.parse((deps.proxy.requestJson as any).mock.calls.at(-1)[1].body as string);
    expect(body.source.pathToken).toBe("signed-token");
  });

  it("leaves non-linked sources untouched", async () => {
    const deps = dependencies();
    deps.proxy.requestJson.mockResolvedValue({ ...PREVIEW, sourceKind: "url" });
    deps.stagedFiles.signGrantedPath = vi.fn().mockReturnValue("signed-token");
    const handlers = registerIpcHandlers(deps);

    await handlers["imports:inspect"]({} as any, {
      kind: "url",
      value: "https://example.test/a.md",
    });

    const body = JSON.parse((deps.proxy.requestJson as any).mock.calls.at(-1)[1].body as string);
    expect(body.pathToken).toBeUndefined();
    expect(deps.stagedFiles.signGrantedPath).not.toHaveBeenCalled();
  });
});

/**
 * 重新定位走的是第二条"把文档指向某个文件"的路 —— 它的门必须和导入一样严。
 */
describe("重新定位原件的签名", () => {
  it("signs the path before forwarding, exactly like import does", async () => {
    const deps = dependencies();
    deps.stagedFiles.signGrantedPath = vi.fn().mockReturnValue("signed-token");
    const handlers = registerIpcHandlers(deps);

    await handlers["documents:relinkOriginal"]({} as any, "00000000-0000-0000-0000-000000000001", {
      path: "/Users/someone/论文.pdf",
    });

    const body = JSON.parse((deps.proxy.requestJson as any).mock.calls.at(-1)[1].body as string);
    expect(body.pathToken).toBe("signed-token");
    expect(deps.stagedFiles.signGrantedPath).toHaveBeenCalledWith("/Users/someone/论文.pdf");
  });

  it("refuses a path no dialog ever granted", async () => {
    const deps = dependencies();
    deps.stagedFiles.signGrantedPath = vi.fn(() => {
      throw new Error("Path was not granted by a file dialog");
    });
    const handlers = registerIpcHandlers(deps);

    await expect(
      handlers["documents:relinkOriginal"]({} as any, "00000000-0000-0000-0000-000000000001", {
        path: "/etc/passwd",
      }),
    ).rejects.toThrow();

    expect(deps.proxy.requestJson).not.toHaveBeenCalled();
  });
});

describe("用系统程序打开原件", () => {
  const DOCUMENT_ID = "00000000-0000-4000-8000-0000000000dd";
  const TOKEN = "test-session-token";
  const ORIGINAL = "/Users/someone/论文/attention.pdf";

  function depsFor(openPath: (path: string) => Promise<string>) {
    const deps = dependencies();
    deps.sessionToken = TOKEN;
    deps.shell = { openExternal: vi.fn(), openPath: vi.fn(openPath) };
    deps.proxy.requestJson = vi.fn().mockResolvedValue({
      path: ORIGINAL,
      pathToken: pathToken(TOKEN, FILE_DOMAIN, ORIGINAL),
      state: "available",
    });
    return deps;
  }

  it("verifies the backend's signature before letting the system open the file", async () => {
    const deps = depsFor(async () => "");
    const handlers = registerIpcHandlers(deps);

    await handlers["documents:openOriginal"]({}, DOCUMENT_ID);

    expect(deps.shell?.openPath).toHaveBeenCalledWith(ORIGINAL);
  });

  it("refuses a path whose signature does not match", async () => {
    const deps = depsFor(async () => "");
    // 后端（或中间人）换了个路径，却带着原来那个路径的签名。
    (deps.proxy.requestJson as ReturnType<typeof vi.fn>).mockResolvedValue({
      path: "/Users/someone/.ssh/id_rsa",
      pathToken: pathToken(TOKEN, FILE_DOMAIN, ORIGINAL),
      state: "available",
    });
    const handlers = registerIpcHandlers(deps);

    await expect(handlers["documents:openOriginal"]({}, DOCUMENT_ID)).rejects.toThrow(/签名无效/);
    // 关键：**没有被打开**。只断言"抛错了"会漏掉"先打开再报错"这种写法。
    expect(deps.shell?.openPath).not.toHaveBeenCalled();
  });

  it("refuses a token signed for a different path in the same domain", async () => {
    const deps = depsFor(async () => "");
    (deps.proxy.requestJson as ReturnType<typeof vi.fn>).mockResolvedValue({
      path: ORIGINAL,
      // 同一个域、但为**另一个路径**签的：签名必须绑在路径上，不能只是一张通行证。
      pathToken: pathToken(TOKEN, FILE_DOMAIN, "/Users/someone/别的.pdf"),
      state: "available",
    });
    const handlers = registerIpcHandlers(deps);

    await expect(handlers["documents:openOriginal"]({}, DOCUMENT_ID)).rejects.toThrow();
    expect(deps.shell?.openPath).not.toHaveBeenCalled();
  });

  it("separates a missing original from a document that never had one", async () => {
    const missing = depsFor(async () => "");
    (missing.proxy.requestJson as ReturnType<typeof vi.fn>).mockResolvedValue({
      path: null,
      pathToken: null,
      state: "missing",
    });
    await expect(
      registerIpcHandlers(missing)["documents:openOriginal"]({}, DOCUMENT_ID),
    ).rejects.toMatchObject({ code: "DOCUMENT_ORIGINAL_MISSING" });

    const none = depsFor(async () => "");
    (none.proxy.requestJson as ReturnType<typeof vi.fn>).mockResolvedValue({
      path: null,
      pathToken: null,
      state: "none",
    });
    await expect(
      registerIpcHandlers(none)["documents:openOriginal"]({}, DOCUMENT_ID),
    ).rejects.toMatchObject({ code: "DOCUMENT_ORIGINAL_NOT_LINKED" });
  });

  it("surfaces the system's failure as a stable reason code", async () => {
    const deps = depsFor(async () => "No application is associated with this file");
    const handlers = registerIpcHandlers(deps);

    // Electron 的约定：成功返回空串，失败返回描述。非空就当作失败 —— 不能吞掉。
    await expect(handlers["documents:openOriginal"]({}, DOCUMENT_ID)).rejects.toMatchObject({
      code: "DOCUMENT_OPEN_FAILED",
    });
  });
});
