import { describe, expect, it, vi } from "vitest";

const exposed: Record<string, unknown> = {};
const invoke = vi.fn().mockResolvedValue({});
const on = vi.fn();
const removeListener = vi.fn();
const send = vi.fn();

vi.mock("electron", () => ({
  contextBridge: { exposeInMainWorld: vi.fn((key: string, value: unknown) => { exposed[key] = value; }) },
  ipcRenderer: { invoke, on, removeListener, send },
}));

describe("preload bridge", () => {
  it("exposes only the frozen typed API without raw Node or IPC helpers", async () => {
    await import("../../preload/index");
    const api = exposed.docmind as any;
    expect(api).toBeDefined();
    expect(Object.isFrozen(api)).toBe(true);
    expect((api as any).require).toBeUndefined();
    expect((api as any).ipcRenderer).toBeUndefined();
    expect((api as any).token).toBeUndefined();
    expect((api as any).fs).toBeUndefined();
    expect(api.settings.get).toBeTypeOf("function");
    expect(api.imports.subscribe).toBeTypeOf("function");
  });

  it("removes a request-specific event listener exactly once on cancellation", async () => {
    const api = exposed.docmind as any;
    const callback = vi.fn();
    const subscription = api.imports.subscribe("00000000-0000-0000-0000-000000000001", 0, callback);
    subscription.cancel();
    subscription.cancel();
    expect(removeListener).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("stream:cancel", subscription.requestId);
  });

  it("keeps one listener per import request when subscribed repeatedly", () => {
    const api = exposed.docmind as any;
    const callback = vi.fn();
    const first = api.imports.subscribe("00000000-0000-0000-0000-000000000001", 0, callback);
    api.imports.subscribe("00000000-0000-0000-0000-000000000001", 1, callback);
    expect(first.cancel).toBeTypeOf("function");
    expect(removeListener).toHaveBeenCalled();
  });

  it("removes its listener after a terminal event", () => {
    const api = exposed.docmind as any;
    const callback = vi.fn();
    api.imports.subscribe("00000000-0000-0000-0000-000000000006", 0, callback);
    const listener = on.mock.calls.at(-1)?.[1] as ((event: unknown, payload: unknown) => void);
    listener({}, { requestId: "r", type: "done", sequence: 1, payload: {} });
    expect(callback).toHaveBeenCalledOnce();
    expect(removeListener).toHaveBeenCalledWith("stream:event:00000000-0000-0000-0000-000000000006", listener);
  });
});
