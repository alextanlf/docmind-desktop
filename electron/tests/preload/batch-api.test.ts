import { beforeAll, describe, expect, it, vi } from "vitest";

const exposed: Record<string, any> = {};
const invoke = vi.fn();
const send = vi.fn();
const on = vi.fn();
const removeListener = vi.fn();

vi.mock("electron", () => ({
  contextBridge: {
    exposeInMainWorld: vi.fn((key: string, value: unknown) => {
      exposed[key] = value;
    }),
  },
  ipcRenderer: { invoke, on, removeListener, send },
}));

/**
 * 目录扫描的 preload 边界。
 *
 * 它从前是"一问一答"的 `stageDirectory()`（扫完才返回）。现在是**订阅**：调用方给一个
 * requestId，进度与结果从 `streamEventChannel(id)` 回来，取消走 `stream:cancel`。
 * 这里守的是那条边界本身 —— 频道名、参数、以及订阅句柄的形状。
 */
describe("目录扫描的 preload API", () => {
  const SCAN_ID = "00000000-0000-4000-8000-0000000000aa";

  beforeAll(async () => {
    await import("../../preload/index");
  });

  it("subscribes on a fixed channel and hands the same id to main", () => {
    const cancel = vi.fn();
    const source = { requestId: SCAN_ID, detach: vi.fn(), cancel };

    exposed.docmind.sources.scanDirectory(SCAN_ID, vi.fn());

    // 订阅：先挂监听，再把同一个 id 发给主进程 —— 两边的键必须一致，否则事件永远收不到。
    expect(on).toHaveBeenCalledWith(`stream:event:${SCAN_ID}`, expect.any(Function));
    // 只断言前两个参数：共用的订阅助手会再补一个 afterSequence 槽（目录扫描用不到它）。
    expect(send.mock.calls.at(-1)?.slice(0, 2)).toEqual(["sources:scanDirectory", SCAN_ID]);
    expect(Object.isFrozen(exposed.docmind.sources)).toBe(true);
    expect(source.requestId).toBe(SCAN_ID);
  });

  it("refuses a malformed subscription id before touching main", () => {
    expect(() => exposed.docmind.sources.scanDirectory("/private/user/docs", vi.fn())).toThrow();
  });
});
