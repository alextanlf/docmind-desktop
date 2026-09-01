import { createRequire } from "node:module";
import { z } from "zod";
import { BackendProxy, DocMindClientError, type StreamSender } from "./backend-proxy";
import { StagedFileService } from "./staged-files";
import { IPC_CHANNELS, streamEventChannel } from "../shared/channels";
import {
  ChatStreamInputSchema,
  CreateImportInputSchema,
  CreateRepositoryInputSchema,
  CreateSessionInputSchema,
  DocumentDetailSchema,
  DocumentInputSchema,
  DocumentSummarySchema,
  ErrorEnvelopeSchema,
  ImportJobSchema,
  MessageSchema,
  ModelConnectionResultSchema,
  ModelSettingsInputSchema,
  ModelStatusSchema,
  RepositorySchema,
  SessionSummarySchema,
  SettingsViewSchema,
  SourcePreviewSchema,
  SourceRefSchema,
  StagedSourceSchema,
  YuqueStatusSchema,
} from "../shared/contracts";

const require = createRequire(import.meta.url);
let electronIpc: {
  ipcMain?: any;
  app?: { on?(event: "before-quit", listener: () => void): void };
  shell?: { openExternal: (url: string) => Promise<void> };
} = {};
try {
  electronIpc = require("electron");
} catch {
  /* tests may run without the Electron binary */
}

type IpcEvent = { sender?: StreamSender };
type Handler = (event: IpcEvent, ...args: any[]) => Promise<any> | any;

export interface IpcDependencies {
  proxy?: BackendProxy;
  backendProxy?: BackendProxy;
  backend?: { request(path: string, init?: RequestInit): Promise<Response> };
  backendManager?: {
    request(path: string, init?: RequestInit): Promise<Response>;
  };
  stagedFiles?: Pick<StagedFileService, "chooseAndStage">;
  files?: Pick<StagedFileService, "chooseAndStage">;
  stagedFileService?: Pick<StagedFileService, "chooseAndStage">;
  shellOpenExternal?: (url: string) => Promise<void>;
  shell?: { openExternal(url: string): Promise<void> };
  ipcMain?: {
    handle(channel: string, listener: Handler): void;
    on(channel: string, listener: Handler): void;
    removeHandler?(channel: string): void;
    removeAllListeners?(channel?: string): void;
  };
  getWebContents?: () => { on?(event: "destroyed", listener: () => void): void } | undefined;
  app?: { on?(event: "before-quit", listener: () => void): void };
}

export type IpcHandlerMap = Record<string, Handler>;

const UUID = z.string().uuid();
const parse = <T>(schema: z.ZodType<T>, value: unknown): T => {
  const result = schema.safeParse(value);
  if (!result.success) throw new DocMindClientError("INVALID_REQUEST", "请求参数无效");
  return result.data;
};

function jsonInit(method: string, body?: unknown): RequestInit {
  const init: RequestInit = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  return init;
}

export function registerIpcHandlers(dependencies: IpcDependencies): IpcHandlerMap {
  const proxy =
    dependencies.proxy ??
    dependencies.backendProxy ??
    new BackendProxy({
      backendManager: dependencies.backend ?? dependencies.backendManager,
    });
  const stagedFiles =
    dependencies.stagedFiles ?? dependencies.files ?? dependencies.stagedFileService;
  if (!stagedFiles) throw new Error("IPC handlers require staged file service");
  const handlers: IpcHandlerMap = {
    [IPC_CHANNELS.settingsGet]: () => proxy.requestJson("/api/settings", {}, SettingsViewSchema),
    [IPC_CHANNELS.settingsSaveModel]: (_event, input) =>
      proxy.requestJson(
        "/api/settings/model",
        jsonInit("PUT", parse(ModelSettingsInputSchema, input)),
        SettingsViewSchema,
      ),
    [IPC_CHANNELS.settingsTestModel]: () =>
      proxy.requestJson("/api/settings/model/test", jsonInit("POST"), ModelConnectionResultSchema),
    [IPC_CHANNELS.settingsClearDiagnostics]: () =>
      proxy.requestVoid("/api/settings/diagnostics/clear", jsonInit("POST")),
    [IPC_CHANNELS.embeddingStatus]: () =>
      proxy.requestJson("/api/embedding/status", {}, ModelStatusSchema),
    [IPC_CHANNELS.embeddingPrepare]: () =>
      proxy.requestJson("/api/embedding/prepare", jsonInit("POST"), ModelStatusSchema),
    [IPC_CHANNELS.yuqueStatus]: () => proxy.requestJson("/api/yuque/status", {}, YuqueStatusSchema),
    [IPC_CHANNELS.yuqueLogin]: () =>
      proxy.requestJson("/api/yuque/login", jsonInit("POST"), YuqueStatusSchema),
    [IPC_CHANNELS.repositoriesList]: () =>
      proxy.requestJson("/api/repositories", {}, z.array(RepositorySchema)),
    [IPC_CHANNELS.repositoriesCreate]: (_event, input) =>
      proxy.requestJson(
        "/api/repositories",
        jsonInit("POST", parse(CreateRepositoryInputSchema, input)),
        RepositorySchema,
      ),
    [IPC_CHANNELS.documentsList]: (_event, repositoryId) => {
      const id = parse(UUID, repositoryId);
      return proxy.requestJson(
        `/api/repositories/${id}/documents`,
        {},
        z.array(DocumentSummarySchema),
      );
    },
    [IPC_CHANNELS.documentsRead]: (_event, documentId) => {
      const id = parse(UUID, documentId);
      return proxy.requestJson(`/api/documents/${id}`, {}, DocumentDetailSchema);
    },
    [IPC_CHANNELS.documentsCreate]: (_event, repositoryId, input) => {
      const id = parse(UUID, repositoryId);
      return proxy.requestJson(
        `/api/repositories/${id}/documents`,
        jsonInit("POST", parse(DocumentInputSchema, input)),
        DocumentDetailSchema,
      );
    },
    [IPC_CHANNELS.documentsUpdate]: (_event, documentId, input) => {
      const id = parse(UUID, documentId);
      return proxy.requestJson(
        `/api/documents/${id}`,
        jsonInit("PUT", parse(DocumentInputSchema, input)),
        DocumentDetailSchema,
      );
    },
    [IPC_CHANNELS.documentsDelete]: (_event, documentId, confirm) => {
      const id = parse(UUID, documentId);
      if (confirm !== true) throw new DocMindClientError("INVALID_REQUEST", "必须确认删除");
      return proxy.requestVoid(`/api/documents/${id}`, jsonInit("DELETE", { confirm: true }));
    },
    [IPC_CHANNELS.importsInspect]: async (_event, input) =>
      SourcePreviewSchema.parse(
        await proxy.requestJson(
          "/api/imports/inspect",
          jsonInit("POST", parse(SourceRefSchema, input)),
          SourcePreviewSchema,
        ),
      ),
    [IPC_CHANNELS.importsCreate]: (_event, input) =>
      proxy.requestJson(
        "/api/imports",
        jsonInit("POST", parse(CreateImportInputSchema, input)),
        ImportJobSchema,
      ),
    [IPC_CHANNELS.importsGet]: (_event, jobId) => {
      const id = parse(UUID, jobId);
      return proxy.requestJson(`/api/imports/${id}`, {}, ImportJobSchema);
    },
    [IPC_CHANNELS.importsRetry]: (_event, jobId) => {
      const id = parse(UUID, jobId);
      return proxy.requestJson(`/api/imports/${id}/retry`, jsonInit("POST"), ImportJobSchema);
    },
    [IPC_CHANNELS.importsCancel]: (_event, jobId) => {
      const id = parse(UUID, jobId);
      return proxy.requestJson(`/api/imports/${id}/cancel`, jsonInit("POST"), ImportJobSchema);
    },
    [IPC_CHANNELS.importsSubscribe]: (event, jobId, afterSequence) => {
      const id = parse(UUID, jobId);
      if (!Number.isInteger(afterSequence) || afterSequence < 0)
        throw new DocMindClientError("INVALID_REQUEST", "事件序号无效");
      return proxy.openStream({
        requestId: id,
        afterSequence,
        route: `/api/imports/${id}/events`,
        headers: { "Last-Event-ID": String(afterSequence) },
        sender: event.sender ?? { send: () => {} },
      });
    },
    [IPC_CHANNELS.chatListSessions]: () =>
      proxy.requestJson("/api/sessions", {}, z.array(SessionSummarySchema)),
    [IPC_CHANNELS.chatCreateSession]: (_event, input) =>
      proxy.requestJson(
        "/api/sessions",
        jsonInit("POST", parse(CreateSessionInputSchema, input)),
        SessionSummarySchema,
      ),
    [IPC_CHANNELS.chatListMessages]: (_event, sessionId) => {
      const id = parse(UUID, sessionId);
      return proxy.requestJson(`/api/sessions/${id}/messages`, {}, z.array(MessageSchema));
    },
    [IPC_CHANNELS.chatStream]: (event, input, afterSequence) => {
      const data = parse(ChatStreamInputSchema, input);
      if (afterSequence !== undefined && (!Number.isInteger(afterSequence) || afterSequence < 0))
        throw new DocMindClientError("INVALID_REQUEST", "事件序号无效");
      const options = {
        requestId: data.requestId,
        sessionId: data.sessionId,
        route: `/api/sessions/${data.sessionId}/messages/stream`,
        body: {
          message: data.message,
          repositoryIds: data.repositoryIds,
          requestId: data.requestId,
        },
        sender: event.sender ?? { send: () => {} },
        ...(afterSequence === undefined
          ? {}
          : {
              afterSequence,
              headers: { "Last-Event-ID": String(afterSequence) },
            }),
      };
      return afterSequence === undefined ? proxy.openStream(options) : proxy.resumeStream(options);
    },
    [IPC_CHANNELS.dialogsChooseSource]: (_event, kind) => {
      if (kind !== "pdf" && kind !== "markdown")
        throw new DocMindClientError("INVALID_REQUEST", "来源类型无效");
      return stagedFiles
        .chooseAndStage(kind)
        .then((value) => (value === null ? null : parse(StagedSourceSchema, value)))
        .catch((error: unknown) => {
          if (error instanceof DocMindClientError) throw error;
          const candidate = error as { code?: unknown; message?: unknown };
          if (typeof candidate.code === "string")
            throw new DocMindClientError(
              candidate.code,
              typeof candidate.message === "string" ? candidate.message : "文件操作失败",
            );
          throw error;
        });
    },
    [IPC_CHANNELS.shellOpenExternal]: async (_event, url) => {
      if (typeof url !== "string") throw new DocMindClientError("INVALID_REQUEST", "链接无效");
      let parsed: URL;
      try {
        parsed = new URL(url);
      } catch {
        throw new DocMindClientError("INVALID_REQUEST", "链接无效");
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:")
        throw new DocMindClientError("INVALID_REQUEST", "仅支持 HTTP(S) 链接");
      return (
        dependencies.shellOpenExternal ??
        dependencies.shell?.openExternal ??
        electronIpc.shell?.openExternal
      )?.(parsed.toString());
    },
    [IPC_CHANNELS.streamCancel]: (_event, requestId) => {
      const id = parse(UUID, requestId);
      proxy.cancel(id);
    },
  };

  const ipcMain = dependencies.ipcMain ?? electronIpc.ipcMain;
  if (ipcMain) {
    for (const [channel, handler] of Object.entries(handlers)) {
      if (
        channel === IPC_CHANNELS.streamCancel ||
        channel === IPC_CHANNELS.importsSubscribe ||
        channel === IPC_CHANNELS.chatStream
      ) {
        ipcMain.on(channel, (event: IpcEvent, ...args: any[]) => {
          try {
            const result = handler(event, ...args);
            if (result && typeof result.then === "function") {
              void result.catch((error: unknown) => emitStreamError(channel, event, args, error));
            }
          } catch (error) {
            emitStreamError(channel, event, args, error);
          }
        });
      } else {
        ipcMain.handle(channel, async (event: IpcEvent, ...args: any[]) => {
          try {
            return { ok: true as const, value: await handler(event, ...args) };
          } catch (error) {
            return { ok: false as const, error: serializeIpcError(error) };
          }
        });
      }
    }
  }
  const webContents = dependencies.getWebContents?.();
  webContents?.on?.("destroyed", () => proxy.cleanup());
  (dependencies.app ?? electronIpc.app)?.on?.("before-quit", () => proxy.cleanup());
  return handlers;
}

export function serializeIpcError(error: unknown): {
  code: string;
  message: string;
  retryable: boolean;
  action?: string;
} {
  if (error instanceof DocMindClientError)
    return sanitizeSerialized({
      code: error.code,
      message: error.message,
      retryable: error.retryable,
      action: error.action,
    });
  if (error instanceof z.ZodError)
    return {
      code: "INVALID_REQUEST",
      message: "请求参数无效",
      retryable: false,
    };
  const candidate = error as { code?: unknown; message?: unknown };
  if (
    candidate &&
    typeof candidate.code === "string" &&
    /^[A-Z0-9_]{1,128}$/.test(candidate.code)
  ) {
    return sanitizeSerialized({
      code: candidate.code,
      message: typeof candidate.message === "string" ? candidate.message : "请求失败",
      retryable: false,
    });
  }
  return {
    code: "BACKEND_REQUEST_FAILED",
    message: "请求失败",
    retryable: false,
  };
}

function sanitizeSerialized(value: {
  code: string;
  message: string;
  retryable: boolean;
  action?: string | null;
}): { code: string; message: string; retryable: boolean; action?: string } {
  const stagedMessages: Record<string, string> = {
    SOURCE_UNSUPPORTED: "不支持此文件类型",
    SOURCE_TOO_LARGE: "文件超过大小限制",
    SOURCE_STAGE_FAILED: "文件暂存失败",
  };
  const clean = (text: string) =>
    text
      .replace(/DOCMIND_SESSION_TOKEN=[^\s]+/g, "DOCMIND_SESSION_TOKEN=[redacted]")
      .replace(/(?:[A-Za-z]:)?\/(?:[^\s/]+\/)+[^\s]*/g, "[path]");
  const result: {
    code: string;
    message: string;
    retryable: boolean;
    action?: string;
  } = {
    code: value.code,
    message: stagedMessages[value.code] ?? clean(value.message),
    retryable: value.retryable,
  };
  if (value.action) result.action = clean(value.action);
  return result;
}

function emitStreamError(channel: string, event: IpcEvent, args: any[], error: unknown): void {
  const requestId = channel === IPC_CHANNELS.chatStream ? args[0]?.requestId : args[0];
  if (typeof requestId !== "string" || !z.string().uuid().safeParse(requestId).success) return;
  const serialized = serializeIpcError(error);
  try {
    event.sender?.send(streamEventChannel(requestId), {
      requestId,
      type: "error",
      sequence: 0,
      payload: serialized,
    });
  } catch {
    // Renderer can disappear while a fire-and-forget stream is starting.
  }
}

export { ErrorEnvelopeSchema };
export { DocMindClientError };
