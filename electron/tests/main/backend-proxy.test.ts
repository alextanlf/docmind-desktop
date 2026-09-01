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
      new Response(
        JSON.stringify({
          error: {
            code: "MODEL_AUTH_FAILED",
            message: "请先配置 API Key",
            retryable: false,
            action: "保存 API Key 后重试",
          },
        }),
        { status: 400 },
      ),
    );
    const proxy = new BackendProxy({
      request: fetch as typeof globalThis.fetch,
      send: vi.fn(),
    });
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
        const text =
          'id: 1\ndata: {"requestId":"r1","type":"progress","sequence":1,"payload":{}}\n\n' +
          'id: 2\ndata: {"requestId":"r1","type":"done","sequence":2,"payload":{}}\n\n';
        const bytes = new TextEncoder().encode(text);
        controller.enqueue(bytes.slice(0, 23));
        controller.enqueue(bytes.slice(23));
        controller.close();
      },
    });
    const proxy = new BackendProxy({
      request: vi.fn().mockResolvedValue(new Response(body)),
    });
    proxy.openStream({
      requestId: "r1",
      route: "/api/imports/00000000-0000-0000-0000-000000000002/events",
      sender: { send: (_channel, event) => sent.push(event) },
    });
    await vi.waitFor(() => expect(proxy.activeStreamCount()).toBe(0));
    expect((sent[0] as any).sequence).toBe(1);
    expect((sent[1] as any).sequence).toBe(2);
  });

  it("allows only one active chat stream per session", () => {
    const proxy = new BackendProxy({
      request: vi.fn(() => new Promise<Response>(() => {})),
    });
    const sessionId = "00000000-0000-0000-0000-000000000003";
    proxy.openStream({
      requestId: "00000000-0000-0000-0000-000000000004",
      sessionId,
      route: `/api/sessions/${sessionId}/messages/stream`,
      sender: { send: vi.fn() },
    });
    expect(() =>
      proxy.openStream({
        requestId: "00000000-0000-0000-0000-000000000005",
        sessionId,
        route: `/api/sessions/${sessionId}/messages/stream`,
        sender: { send: vi.fn() },
      }),
    ).toThrow(/活动聊天流/);
    proxy.cleanup();
  });

  it("starts import replay after the requested sequence", async () => {
    const sent: unknown[] = [];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const text =
          'data: {"requestId":"r1","type":"progress","sequence":1,"payload":{}}\n\n' +
          'data: {"requestId":"r1","type":"progress","sequence":3,"payload":{}}\n\n' +
          'data: {"requestId":"r1","type":"done","sequence":4,"payload":{}}\n\n';
        controller.enqueue(new TextEncoder().encode(text));
        controller.close();
      },
    });
    const proxy = new BackendProxy({
      request: vi.fn().mockResolvedValue(new Response(body)),
    });
    proxy.openStream({
      requestId: "r1",
      route: "/api/imports/00000000-0000-0000-0000-000000000002/events",
      afterSequence: 2,
      sender: { send: (_channel, event) => sent.push(event) },
    });
    await vi.waitFor(() => expect(proxy.activeStreamCount()).toBe(0));
    expect(sent.map((event) => (event as any).sequence)).toEqual([3, 4]);
  });

  it("suppresses events that arrive after cancellation even when fetch ignores AbortSignal", async () => {
    let resolveResponse!: (response: Response) => void;
    const responsePromise = new Promise<Response>((resolve) => {
      resolveResponse = resolve;
    });
    const sent: unknown[] = [];
    const proxy = new BackendProxy({
      request: vi.fn().mockReturnValue(responsePromise),
    });
    const requestId = "00000000-0000-0000-0000-000000000007";
    proxy.openStream({
      requestId,
      route: "/api/imports/00000000-0000-0000-0000-000000000002/events",
      sender: { send: (_channel, event) => sent.push(event) },
    });
    proxy.cancel(requestId);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            `data: {"requestId":"${requestId}","type":"done","sequence":1,"payload":{}}\n\n`,
          ),
        );
        controller.close();
      },
    });
    resolveResponse(new Response(body));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sent).toEqual([]);
  });

  it("does not let a canceled stream remove or error a replacement stream with the same request ID", async () => {
    let rejectFirst!: (error: Error) => void;
    let resolveSecond!: (response: Response) => void;
    const first = new Promise<Response>((_resolve, reject) => {
      rejectFirst = reject;
    });
    const second = new Promise<Response>((resolve) => {
      resolveSecond = resolve;
    });
    const sent: unknown[] = [];
    const request = vi
      .fn()
      .mockImplementationOnce(() => first)
      .mockImplementationOnce(() => second);
    const proxy = new BackendProxy({ request });
    const requestId = "00000000-0000-0000-0000-000000000008";
    const options = {
      requestId,
      route: "/api/imports/00000000-0000-0000-0000-000000000002/events",
      sender: { send: (_channel: string, event: unknown) => sent.push(event) },
    };
    proxy.openStream(options);
    proxy.cancel(requestId);
    proxy.openStream(options);
    rejectFirst(new Error("first failed"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(proxy.activeStreamCount()).toBe(1);
    expect(sent).toEqual([]);
    resolveSecond(
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.close();
          },
        }),
      ),
    );
    proxy.cleanup();
  });

  it("does not forward raw stream error messages containing paths", async () => {
    const sent: unknown[] = [];
    const requestId = "00000000-0000-0000-0000-000000000010";
    const proxy = new BackendProxy({
      request: vi
        .fn()
        .mockRejectedValue(new Error("failed at /private/secret")),
    });
    proxy.openStream({
      requestId,
      route: "/api/imports/00000000-0000-0000-0000-000000000002/events",
      sender: { send: (_channel, event) => sent.push(event) },
    });
    await vi.waitFor(() => expect(proxy.activeStreamCount()).toBe(0));
    expect(sent[0]).toMatchObject({
      payload: { code: "BACKEND_REQUEST_FAILED", message: "后端请求失败" },
    });
    expect(JSON.stringify(sent)).not.toContain("/private/secret");
  });

  it("emits a terminal protocol error when a stream ends without a done/error event", async () => {
    const sent: unknown[] = [];
    const requestId = "00000000-0000-0000-0000-000000000011";
    const proxy = new BackendProxy({
      request: vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.close();
            },
          }),
        ),
      ),
    });
    proxy.openStream({
      requestId,
      route: "/api/imports/00000000-0000-0000-0000-000000000002/events",
      sender: { send: (_channel, event) => sent.push(event) },
    });
    await vi.waitFor(() => expect(proxy.activeStreamCount()).toBe(0));
    expect(sent[0]).toMatchObject({
      requestId,
      type: "error",
      payload: { code: "BACKEND_PROTOCOL_ERROR" },
    });
  });
});
