import { beforeAll, describe, expect, it, vi } from "vitest";

const exposed: Record<string, any> = {};
const invoke = vi.fn();

vi.mock("electron", () => ({
  contextBridge: {
    exposeInMainWorld: vi.fn((key: string, value: unknown) => {
      exposed[key] = value;
    }),
  },
  ipcRenderer: {
    invoke,
    on: vi.fn(),
    removeListener: vi.fn(),
    send: vi.fn(),
  },
}));

describe("batch preload API", () => {
  beforeAll(async () => {
    await import("../../preload/index");
  });

  it("exposes a frozen zero-argument directory staging method on a fixed channel", async () => {
    invoke.mockResolvedValueOnce({
      ok: true,
      value: {
        collectionId: "00000000-0000-0000-0000-000000000021",
        displayName: "docs",
        itemCount: 3,
        totalBytes: 42,
      },
    });

    const result = await exposed.docmind.sources.stageDirectory();

    expect(invoke).toHaveBeenLastCalledWith("sources:stageDirectory");
    expect(result).toEqual({
      collectionId: "00000000-0000-0000-0000-000000000021",
      displayName: "docs",
      itemCount: 3,
      totalBytes: 42,
    });
    expect(Object.isFrozen(exposed.docmind.sources)).toBe(true);
  });

  it("rejects a malformed collection response at the preload boundary", async () => {
    invoke.mockResolvedValueOnce({
      ok: true,
      value: {
        collectionId: "not-a-uuid",
        displayName: "/private/user/docs",
        itemCount: 3,
        totalBytes: 42,
      },
    });

    await expect(exposed.docmind.sources.stageDirectory()).rejects.toBeDefined();
  });
});
