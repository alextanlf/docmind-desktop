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
describe("search preload API", () => {
  beforeAll(async () => {
    await import("../../preload/index");
  });
  it("validates settings without exposing a key", async () => {
    const value = {
      model: { preset: "custom", baseUrl: "", model: "", timeoutSeconds: 30 },
      hasApiKey: false,
      dataPath: "/tmp",
      screenshotCount: 0,
      webSearch: {
        mode: "ask",
        maxResults: 5,
        hasApiKey: true,
        queryRewrite: true,
        searxngUrl: "https://searx.example.com",
        modelSearchAvailable: false,
        modelSearchLabel: "",
        freeFallbackAvailable: true,
      },
    };
    invoke.mockResolvedValueOnce({ ok: true, value });
    const result = await exposed.docmind.settings.saveWebSearch({
      mode: "ask",
      maxResults: 5,
      queryRewrite: true,
      searxngUrl: "https://searx.example.com",
      apiKey: "secret",
    });
    expect(invoke).toHaveBeenLastCalledWith("settings:saveWebSearch", {
      mode: "ask",
      maxResults: 5,
      queryRewrite: true,
      searxngUrl: "https://searx.example.com",
      apiKey: "secret",
    });
    expect(JSON.stringify(result)).not.toContain("secret");
  });
});
