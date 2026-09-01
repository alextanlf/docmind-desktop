import { describe, expect, it, vi } from "vitest";
import { registerIpcHandlers } from "../../main/ipc-handlers";

function dependencies() {
  return {
    proxy: {
      requestJson: vi.fn().mockResolvedValue({}),
      requestVoid: vi.fn().mockResolvedValue(undefined),
      openStream: vi.fn().mockReturnValue({ requestId: "00000000-0000-0000-0000-000000000001", cancel: vi.fn() }),
      cancel: vi.fn(),
      cleanup: vi.fn(),
    },
    stagedFiles: { chooseAndStage: vi.fn() },
    shellOpenExternal: vi.fn(),
    ipcMain: { handle: vi.fn(), on: vi.fn(), removeHandler: vi.fn(), removeAllListeners: vi.fn() },
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
});
