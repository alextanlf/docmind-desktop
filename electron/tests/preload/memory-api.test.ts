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

describe("memory preload API", () => {
  beforeAll(async () => {
    await import("../../preload/index");
  });
  it("uses fixed channels and validates ids", async () => {
    invoke.mockResolvedValueOnce({ ok: true, value: null });
    await exposed.docmind.memory.getSummary("00000000-0000-0000-0000-000000000025");
    expect(invoke).toHaveBeenLastCalledWith(
      "memory:getSummary",
      "00000000-0000-0000-0000-000000000025",
    );
    invoke.mockClear();
    expect(() => exposed.docmind.memory.getDistillation("not-a-uuid")).toThrow("INVALID_REQUEST");
    expect(invoke).not.toHaveBeenCalled();
  });
  it("rejects absolute local paths from the backend", async () => {
    invoke.mockResolvedValueOnce({
      ok: true,
      value: {
        id: "00000000-0000-0000-0000-000000000026",
        sessionId: null,
        title: "x",
        content: "x",
        keyPoints: [],
        sources: [],
        repositoryIds: [],
        state: "saved",
        storageTarget: "local",
        localPath: "/Users/private/x.md",
        documentId: null,
        yuqueUrl: null,
        errorCode: null,
        retryable: false,
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    });
    await expect(
      exposed.docmind.memory.getDistillation("00000000-0000-0000-0000-000000000026"),
    ).rejects.toBeDefined();
  });
});
