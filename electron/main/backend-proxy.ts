import { z } from "zod";
import {
  BackendEventEnvelopeSchema,
  type BackendEventEnvelope,
  type StreamSubscription,
  ErrorEnvelopeSchema,
} from "../shared/contracts";
import { streamEventChannel } from "../shared/channels";

export interface StreamSender {
  send(channel: string, ...args: unknown[]): void;
  once?(event: "destroyed", listener: () => void): void;
  on?(event: "destroyed", listener: () => void): void;
  removeListener?(event: "destroyed", listener: () => void): void;
}

export class DocMindClientError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly action?: string;

  constructor(code: string, message: string, retryable = false, action?: string | null) {
    super(message);
    this.name = "DocMindClientError";
    this.code = code;
    this.retryable = retryable;
    if (action) this.action = action;
    Object.setPrototypeOf(this, new.target.prototype);
  }
  toJSON() {
    return {
      code: this.code,
      message: this.message,
      retryable: this.retryable,
      action: this.action,
    };
  }
}

export type RequestFn = (path: string, init?: RequestInit) => Promise<Response>;

export interface OpenStreamOptions {
  requestId: string;
  route: string;
  body?: unknown;
  sender: StreamSender;
  controller?: AbortController;
  sessionId?: string;
  headers?: HeadersInit;
  afterSequence?: number;
}

type ActiveStream = {
  controller: AbortController;
  sender: StreamSender;
  destroyed?: () => void;
  sessionId?: string;
  lastSequence: number;
};

const STREAM_ROUTE =
  /^\/api\/(?:imports\/[0-9a-f-]{36}\/events|import-batches\/[0-9a-f-]{36}\/events|sessions\/[0-9a-f-]{36}\/messages\/stream)$/;

export class BackendProxy {
  private readonly requestFn: RequestFn;
  private readonly sendFn?: (sender: StreamSender, channel: string, payload: unknown) => void;
  private readonly active = new Map<string, ActiveStream>();
  private readonly chatSessions = new Map<string, string>();

  constructor(opts: {
    request?: RequestFn;
    backendManager?: {
      request(path: string, init?: RequestInit): Promise<Response>;
    };
    send?: (sender: StreamSender, channel: string, payload: unknown) => void;
  }) {
    const request = opts.request ?? opts.backendManager?.request.bind(opts.backendManager);
    if (!request) throw new Error("BackendProxy requires a request function");
    this.requestFn = request;
    this.sendFn = opts.send;
  }

  async requestJson<T>(
    route: string,
    initOrSchema: RequestInit | z.ZodType<T> = {},
    schema?: z.ZodType<T>,
  ): Promise<T> {
    let init: RequestInit;
    let parser: z.ZodType<T> | undefined;
    if (isSchema(initOrSchema)) {
      init = {};
      parser = initOrSchema;
    } else {
      init = initOrSchema;
      parser = schema;
    }
    let response: Response;
    try {
      response = await this.requestFn(route, init);
    } catch (error) {
      if (error instanceof DocMindClientError) throw error;
      throw new DocMindClientError("BACKEND_UNAVAILABLE", "后端暂不可用，请稍后重试", true);
    }
    const parsed = await this.parseResponse(response);
    if (!parser) return parsed as T;
    try {
      return parser.parse(parsed);
    } catch {
      throw new DocMindClientError("BACKEND_PROTOCOL_ERROR", "后端响应格式无效", true);
    }
  }

  async requestVoid(route: string, init: RequestInit = {}): Promise<void> {
    let response: Response;
    try {
      response = await this.requestFn(route, init);
    } catch (error) {
      if (error instanceof DocMindClientError) throw error;
      throw new DocMindClientError("BACKEND_UNAVAILABLE", "后端暂不可用，请稍后重试", true);
    }
    if (response.ok || response.status === 204) return;
    await this.throwResponseError(response);
  }

  openStream(options: OpenStreamOptions): StreamSubscription {
    if (!STREAM_ROUTE.test(options.route.split("?")[0]))
      throw new DocMindClientError("INVALID_REQUEST", "流式请求路径无效");
    if (this.active.has(options.requestId))
      throw new DocMindClientError("STREAM_ACTIVE", "该请求已有活动流");
    const isChat = options.route.includes("/messages/stream");
    const routeSession = options.route.match(/^\/api\/sessions\/([^/]+)\/messages\/stream$/)?.[1];
    const sessionId = options.sessionId ?? routeSession;
    if (isChat && sessionId) {
      const current = this.chatSessions.get(sessionId);
      if (current && this.active.has(current))
        throw new DocMindClientError("CHAT_STREAM_ACTIVE", "该会话已有活动聊天流");
      this.chatSessions.set(sessionId, options.requestId);
    }
    const controller = options.controller ?? new AbortController();
    const stream: ActiveStream = {
      controller,
      sender: options.sender,
      sessionId,
      lastSequence: options.afterSequence ?? 0,
    };
    this.active.set(options.requestId, stream);
    const destroyed = () => this.cancel(options.requestId);
    stream.destroyed = destroyed;
    if (options.sender.once) options.sender.once("destroyed", destroyed);
    else options.sender.on?.("destroyed", destroyed);
    void this.consume(options.requestId, options.route, options.body, stream, options.headers);
    return {
      requestId: options.requestId,
      detach: () => undefined,
      cancel: () => this.cancel(options.requestId),
    };
  }

  resumeStream(options: OpenStreamOptions): StreamSubscription {
    const current = this.active.get(options.requestId);
    if (!current) return this.openStream(options);

    current.controller.abort();
    if (current.destroyed) current.sender.removeListener?.("destroyed", current.destroyed);
    const controller = options.controller ?? new AbortController();
    const stream: ActiveStream = {
      controller,
      sender: options.sender,
      sessionId: current.sessionId,
      lastSequence: options.afterSequence ?? current.lastSequence,
    };
    this.active.set(options.requestId, stream);
    const destroyed = () => this.cancel(options.requestId);
    stream.destroyed = destroyed;
    if (stream.sender.once) stream.sender.once("destroyed", destroyed);
    else stream.sender.on?.("destroyed", destroyed);
    void this.consume(options.requestId, options.route, options.body, stream, options.headers);
    return {
      requestId: options.requestId,
      detach: () => undefined,
      cancel: () => this.cancel(options.requestId),
    };
  }

  cancel(requestId: string): void {
    const stream = this.active.get(requestId);
    if (!stream) return;
    stream.controller.abort();
    this.remove(requestId, stream);
  }

  cleanup(): void {
    for (const [requestId, stream] of this.active) {
      stream.controller.abort();
      this.remove(requestId, stream);
    }
  }

  close(): void {
    this.cleanup();
  }

  shutdown(): void {
    this.cleanup();
  }

  activeStreamCount(): number {
    return this.active.size;
  }

  private remove(requestId: string, stream: ActiveStream): void {
    if (this.active.get(requestId) !== stream) return;
    this.active.delete(requestId);
    if (stream.sessionId && this.chatSessions.get(stream.sessionId) === requestId)
      this.chatSessions.delete(stream.sessionId);
    if (stream.destroyed) stream.sender.removeListener?.("destroyed", stream.destroyed);
  }

  private async parseResponse(response: Response): Promise<unknown> {
    if (!response.ok) return this.throwResponseError(response);
    if (response.status === 204) return undefined;
    try {
      return await response.json();
    } catch {
      throw new DocMindClientError("BACKEND_PROTOCOL_ERROR", "后端响应格式无效", true);
    }
  }

  private async throwResponseError(response: Response): Promise<never> {
    let code = "BACKEND_REQUEST_FAILED";
    let message = `后端请求失败（${response.status}）`;
    let retryable = response.status >= 500;
    let action: string | null | undefined;
    try {
      const parsed = ErrorEnvelopeSchema.parse(await response.json());
      ({ code, message, retryable, action } = parsed.error);
    } catch {
      // retain generic sanitized error
    }
    throw new DocMindClientError(code, message, retryable, action);
  }

  private async consume(
    requestId: string,
    route: string,
    body: unknown,
    stream: ActiveStream,
    extraHeaders?: HeadersInit,
  ): Promise<void> {
    try {
      const streamHeaders = new Headers(extraHeaders);
      streamHeaders.set("Accept", "text/event-stream");
      const init: RequestInit = {
        signal: stream.controller.signal,
        headers: streamHeaders,
      };
      if (body !== undefined) {
        init.method = "POST";
        streamHeaders.set("Content-Type", "application/json");
        init.body = JSON.stringify(body);
      }
      const response = await this.requestFn(route, init);
      if (this.active.get(requestId) !== stream) return;
      if (!response.ok) await this.throwResponseError(response);
      if (!response.body)
        throw new DocMindClientError("BACKEND_PROTOCOL_ERROR", "后端流式响应为空", true);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let terminalSeen = false;
      while (true) {
        const result = await reader.read();
        if (this.active.get(requestId) !== stream) return;
        if (result.done) break;
        buffer += decoder.decode(result.value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const event = parseSseFrame(frame);
          if (!event || (event.requestId !== requestId && route.includes("/messages/stream")))
            continue;
          if (event.sequence <= stream.lastSequence) continue;
          if (this.active.get(requestId) !== stream) return;
          stream.lastSequence = event.sequence;
          this.send(stream.sender, streamEventChannel(requestId), event);
          if (event.type === "done" || event.type === "error") {
            terminalSeen = true;
            this.remove(requestId, stream);
            return;
          }
        }
      }
      buffer += decoder.decode();
      const finalEvent = parseSseFrame(buffer);
      if (
        finalEvent &&
        finalEvent.sequence > stream.lastSequence &&
        this.active.get(requestId) === stream
      ) {
        stream.lastSequence = finalEvent.sequence;
        this.send(stream.sender, streamEventChannel(requestId), finalEvent);
        terminalSeen = finalEvent.type === "done" || finalEvent.type === "error";
      }
      if (!terminalSeen && this.active.get(requestId) === stream) {
        this.send(stream.sender, streamEventChannel(requestId), {
          requestId,
          type: "error",
          sequence: stream.lastSequence + 1,
          payload: {
            code: "BACKEND_PROTOCOL_ERROR",
            message: "后端流式响应未正常结束",
            retryable: true,
          },
        } satisfies BackendEventEnvelope);
      }
    } catch (error) {
      if (
        !(error instanceof DOMException && error.name === "AbortError") &&
        !(error instanceof Error && error.name === "AbortError")
      ) {
        if (this.active.get(requestId) === stream) {
          const current = stream;
          const event: BackendEventEnvelope = {
            requestId,
            type: "error",
            sequence: current.lastSequence + 1,
            payload:
              error instanceof DocMindClientError
                ? {
                    code: error.code,
                    message: sanitizeStreamErrorMessage(error.message),
                    retryable: error.retryable,
                    ...(error.action ? { action: error.action } : {}),
                  }
                : {
                    code: "BACKEND_REQUEST_FAILED",
                    message: "后端请求失败",
                    retryable: true,
                  },
          };
          this.send(current.sender, streamEventChannel(requestId), event);
        }
      }
    } finally {
      if (this.active.get(requestId) === stream) this.remove(requestId, stream);
    }
  }

  private send(sender: StreamSender, channel: string, payload: unknown): void {
    try {
      if (this.sendFn) this.sendFn(sender, channel, payload);
      else sender.send(channel, payload);
    } catch {
      // Renderer may disappear between read and send; cleanup runs from destroyed.
    }
  }
}

function sanitizeStreamErrorMessage(message: string): string {
  return message
    .replace(/DOCMIND_SESSION_TOKEN=[^\s]+/g, "DOCMIND_SESSION_TOKEN=[redacted]")
    .replace(/(?:[A-Za-z]:)?\/(?:[^\s/]+\/)+[^\s]*/g, "[path]");
}

function isSchema(value: unknown): value is z.ZodType<unknown> {
  return Boolean(
    value && typeof value === "object" && typeof (value as z.ZodType).parse === "function",
  );
}

export function parseSseFrame(frame: string): BackendEventEnvelope | null {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  try {
    return BackendEventEnvelopeSchema.parse(JSON.parse(data));
  } catch {
    return null;
  }
}

export async function collectSse(
  chunks: Iterable<string | Uint8Array> | AsyncIterable<string | Uint8Array>,
): Promise<BackendEventEnvelope[]> {
  const result: BackendEventEnvelope[] = [];
  let buffer = "";
  const decoder = new TextDecoder();
  for await (const chunk of chunks) {
    buffer += typeof chunk === "string" ? chunk : decoder.decode(chunk, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const event = parseSseFrame(frame);
      if (event && (!result.length || event.sequence > result[result.length - 1].sequence))
        result.push(event);
    }
  }
  buffer += decoder.decode();
  const event = parseSseFrame(buffer);
  if (event && (!result.length || event.sequence > result[result.length - 1].sequence))
    result.push(event);
  return result;
}
