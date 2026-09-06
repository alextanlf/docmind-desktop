import { describe, expect, it, vi } from "vitest";
import {
  EventEnvelopeSchema,
  BackendEventEnvelopeSchema,
  SourcePreviewSchema,
  CitationSchema,
  RepositorySchema,
} from "../../shared/contracts";

const exposed: Record<string, unknown> = {};
const invoke = vi.fn().mockResolvedValue({});
const on = vi.fn();
const removeListener = vi.fn();
const send = vi.fn();

vi.mock("electron", () => ({
  contextBridge: {
    exposeInMainWorld: vi.fn((key: string, value: unknown) => {
      exposed[key] = value;
    }),
  },
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

  it("detaches a request listener without cancelling its backend stream", () => {
    const api = exposed.docmind as any;
    const subscription = api.chat.stream(
      {
        requestId: "00000000-0000-0000-0000-000000000013",
        sessionId: "00000000-0000-0000-0000-000000000014",
        message: "问题",
        repositoryIds: ["00000000-0000-0000-0000-000000000015"],
      },
      vi.fn(),
    );

    subscription.detach();

    expect(removeListener).toHaveBeenCalledWith(
      "stream:event:00000000-0000-0000-0000-000000000013",
      expect.any(Function),
    );
    expect(send).not.toHaveBeenCalledWith("stream:cancel", subscription.requestId);
  });

  it("releases a detached chat subscription so a remount can reattach without cancelling it", () => {
    const api = exposed.docmind as any;
    const input = {
      requestId: "00000000-0000-0000-0000-000000000017",
      sessionId: "00000000-0000-0000-0000-000000000018",
      message: "问题",
      repositoryIds: ["00000000-0000-0000-0000-000000000019"],
    };
    const first = api.chat.stream(input, vi.fn());

    first.detach();
    const second = api.chat.stream(input, vi.fn(), 1);

    expect(second.requestId).toBe(input.requestId);
    expect(send).not.toHaveBeenCalledWith("stream:cancel", input.requestId);
  });

  it("requires a backend-reported indexed document count", () => {
    const parsed = RepositorySchema.parse({
      id: "00000000-0000-0000-0000-000000000016",
      yuqueId: null,
      name: "SwiftUI",
      description: null,
      yuqueUrl: null,
      documentCount: 3,
      indexedDocumentCount: 2,
      syncStatus: "已同步",
      createdAt: "2026-08-31T00:00:00Z",
      updatedAt: "2026-08-31T00:00:00Z",
    });

    expect(parsed.indexedDocumentCount).toBe(2);
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
    const listener = on.mock.calls.at(-1)?.[1] as (event: unknown, payload: unknown) => void;
    listener(
      {},
      {
        requestId: "00000000-0000-0000-0000-000000000006",
        type: "done",
        sequence: 1,
        payload: {},
      },
    );
    expect(callback).toHaveBeenCalledOnce();
    expect(removeListener).toHaveBeenCalledWith(
      "stream:event:00000000-0000-0000-0000-000000000006",
      listener,
    );
  });

  it("requires UUID request IDs for renderer events while retaining a separate opaque backend schema", () => {
    expect(
      EventEnvelopeSchema.safeParse({
        requestId: "opaque",
        type: "done",
        sequence: 1,
        payload: {},
      }).success,
    ).toBe(false);
    expect(
      BackendEventEnvelopeSchema.safeParse({
        request_id: "opaque",
        type: "done",
        sequence: 1,
        payload: {},
      }).success,
    ).toBe(true);
  });

  it("masks staged source URLs in the shared contract", () => {
    const parsed = SourcePreviewSchema.parse({
      title: "指南",
      sourceKind: "staged_file",
      sourceUrl: "/private/staging/file.md",
      mediaType: "text/markdown",
      sizeBytes: 1,
      fingerprint: "a".repeat(64),
      warnings: [],
    });
    expect((parsed as any).sourceUrl).toBeNull();
  });

  it("masks local staged paths from citation responses", () => {
    const parsed = CitationSchema.parse({
      sourceId: "S1",
      chunkId: "chunk-1",
      documentId: "00000000-0000-0000-0000-000000000009",
      title: "指南",
      sectionPath: null,
      pageNumber: null,
      excerpt: "内容",
      sourceUrl: "file:///private/staging/guide.md",
    });
    expect((parsed as any).sourceUrl).toBeNull();
  });

  it("unwraps a resolved IPC error envelope without losing stable fields", async () => {
    const api = exposed.docmind as any;
    invoke.mockResolvedValueOnce({
      ok: false,
      error: {
        code: "MODEL_AUTH_FAILED",
        message: "请先配置 API Key",
        retryable: false,
        action: "保存 API Key 后重试",
      },
    });
    await expect(api.settings.get()).rejects.toEqual({
      code: "MODEL_AUTH_FAILED",
      message: "请先配置 API Key",
      retryable: false,
      action: "保存 API Key 后重试",
    });
  });

  it("removes terminal listeners even when the event callback throws", () => {
    const api = exposed.docmind as any;
    const callback = vi.fn(() => {
      throw new Error("renderer callback failed");
    });
    api.imports.subscribe("00000000-0000-0000-0000-000000000012", 0, callback);
    const listener = on.mock.calls.at(-1)?.[1] as (event: unknown, payload: unknown) => void;
    expect(() =>
      listener(
        {},
        {
          requestId: "00000000-0000-0000-0000-000000000012",
          type: "done",
          sequence: 1,
          payload: {},
        },
      ),
    ).toThrow("renderer callback failed");
    expect(removeListener).toHaveBeenCalledWith(
      "stream:event:00000000-0000-0000-0000-000000000012",
      listener,
    );
  });
});
