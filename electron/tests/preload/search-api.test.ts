import { beforeAll, describe, expect, it, vi } from "vitest";

const exposed: Record<string, any> = {};
const invoke = vi.fn();
vi.mock("electron", () => ({
  contextBridge: {
    exposeInMainWorld: vi.fn((key: string, value: unknown) => {
      exposed[key] = value;
    }),
  },
  ipcRenderer: { invoke, on: vi.fn(), removeListener: vi.fn(), send: vi.fn() },
}));

const SETTINGS_VIEW = {
  model: { preset: "custom", baseUrl: "", model: "", timeoutSeconds: 30 },
  hasApiKey: false,
  dataPath: "/tmp",
  screenshotCount: 0,
};

const RUN_ID = "11111111-1111-4111-8111-111111111111";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";

describe("search preload API", () => {
  beforeAll(async () => {
    await import("../../preload/index");
  });

  it("no longer exposes a web-search settings writer", () => {
    // 设置页的联网分区已删除。写入口不能当孤儿留着：它会打到已经删掉的
    // /api/settings/web-search，前端只会拿到 404，而且类型上还自称能用。
    expect(exposed.docmind.settings.saveWebSearch).toBeUndefined();
  });

  it("drops a legacy web-search section from the settings view", async () => {
    // 旧后端仍会回 webSearch；schema 必须把它剥掉，而不是透出成"设置里还有联网
    // 分区"。zod 的 object 默认 strip —— 这条把该行为锁死。
    invoke.mockResolvedValueOnce({
      ok: true,
      value: { ...SETTINGS_VIEW, webSearch: { mode: "auto", maxResults: 5 } },
    });

    const result = await exposed.docmind.settings.get();

    expect(result.webSearch).toBeUndefined();
    expect(result.dataPath).toBe("/tmp");
  });

  it("still routes search-run lookups through the webSearch channel", async () => {
    // 结果查看/导入那套（SearchResultPicker）不在本次删除范围内，必须仍在。
    invoke.mockResolvedValueOnce({
      ok: true,
      value: { id: RUN_ID, status: "completed", results: [] },
    });

    await exposed.docmind.webSearch.getRun(RUN_ID, SESSION_ID);

    expect(invoke).toHaveBeenLastCalledWith("webSearch:getRun", RUN_ID, SESSION_ID);
  });
});
