import { describe, expect, it, vi } from "vitest";
import { BackendProxy, collectSse } from "../../main/backend-proxy";

describe("BackendProxy", () => {
  it("parses split SSE frames, preserves order, and ignores duplicates", async () => {
    const events = await collectSse([
      'id: 1\ndata: {"requestId":"00000000-0000-0000-0000-000000000001","type":"pro',
      'gress","sequence":1,"payload":{"progress":20}}\n\n',
      'id: 1\ndata: {"requestId":"00000000-0000-0000-0000-000000000001","type":"progress","sequence":1,"payload":{"progress":20}}\n\n',
      'id: 2\ndata: {"requestId":"00000000-0000-0000-0000-000000000001","type":"done","sequence":2,"payload":{}}\n\n',
    ]);
    expect(events.map((event) => event.sequence)).toEqual([1, 2]);
  });

  it("aborts a matching stream and removes its controller on cancellation", async () => {
    const abort = vi.fn();
    const fetch = vi.fn(() => new Promise<Response>(() => {}));
    const proxy = new BackendProxy({
      request: fetch as typeof globalThis.fetch,
      send: vi.fn(),
    });
    const subscription = proxy.openStream({
      requestId: "00000000-0000-0000-0000-000000000001",
      route: "/api/imports/00000000-0000-0000-0000-000000000002/events",
      body: undefined,
      sender: { send: vi.fn() },
      controller: { abort } as unknown as AbortController,
    });
    proxy.cancel(subscription.requestId);
    expect(abort).toHaveBeenCalledTimes(1);
    expect(proxy.activeStreamCount()).toBe(0);
  });

  it("maps backend JSON errors to serializable client errors", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "MODEL_AUTH_FAILED", message: "请先配置 API Key", retryable: false, action: "保存 API Key 后重试" } }), { status: 400 }),
    );
    const proxy = new BackendProxy({ request: fetch as typeof globalThis.fetch, send: vi.fn() });
    await expect(proxy.requestJson("/api/settings")).rejects.toMatchObject({
      code: "MODEL_AUTH_FAILED",
      retryable: false,
      action: "保存 API Key 后重试",
    });
  });

  it("forwards ordered events from a byte-split response and removes the stream at terminal", async () => {
    const sent: unknown[] = [];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const text = 'id: 1\ndata: {"requestId":"r1","type":"progress","sequence":1,"payload":{}}\n\n' +
          'id: 2\ndata: {"requestId":"r1","type":"done","sequence":2,"payload":{}}\n\n';
        const bytes = new TextEncoder().encode(text);
        controller.enqueue(bytes.slice(0, 23));
        controller.enqueue(bytes.slice(23));
        controller.close();
      },
    });
    const proxy = new BackendProxy({ request: vi.fn().mockResolvedValue(new Response(body)) });
    proxy.openStream({ requestId: "r1", route: "/api/imports/00000000-0000-0000-0000-000000000002/events", sender: { send: (_channel, event) => sent.push(event) } });
    await vi.waitFor(() => expect(proxy.activeStreamCount()).toBe(0));
    expect((sent[0] as any).sequence).toBe(1);
    expect((sent[1] as any).sequence).toBe(2);
  });

  it("allows only one active chat stream per session", () => {
    const proxy = new BackendProxy({ request: vi.fn(() => new Promise<Response>(() => {})) });
    const sessionId = "00000000-0000-0000-0000-000000000003";
    proxy.openStream({ requestId: "00000000-0000-0000-0000-000000000004", sessionId, route: `/api/sessions/${sessionId}/messages/stream`, sender: { send: vi.fn() } });
    expect(() => proxy.openStream({ requestId: "00000000-0000-0000-0000-000000000005", sessionId, route: `/api/sessions/${sessionId}/messages/stream`, sender: { send: vi.fn() } })).toThrow(/活动聊天流/);
    proxy.cleanup();
  });
});
