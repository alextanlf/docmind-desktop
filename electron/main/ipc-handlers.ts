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
  StagedCollectionSchema,
  YuqueStatusSchema,
  BatchImportSchema,
  BatchItemPageSchema,
  CreateBatchInputSchema,
  ConfirmBatchInputSchema,
  RetryBatchInputSchema,
  DistillationEditSchema,
  DistillationTargetSchema,
  DistillationViewSchema,
  MemoryItemPageSchema,
  MemoryListInputSchema,
  SessionMemorySummarySchema,
  WebSearchSettingsInputSchema,
  WebSearchRunSchema,
  SearchImportInputSchema,
  ChatSearchInputSchema,
  OllamaStatusSchema, OllamaModelsSchema, OllamaPullInputSchema, OllamaPullSchema,
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
  stagedFiles?: Pick<StagedFileService, "chooseAndStage" | "stageDirectory">;
  files?: Pick<StagedFileService, "chooseAndStage" | "stageDirectory">;
  stagedFileService?: Pick<StagedFileService, "chooseAndStage" | "stageDirectory">;
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
    [IPC_CHANNELS.ollamaStatus]: () => proxy.requestJson("/api/ollama/status", {}, OllamaStatusSchema),
    [IPC_CHANNELS.ollamaModels]: () => proxy.requestJson("/api/ollama/models", {}, OllamaModelsSchema),
    [IPC_CHANNELS.ollamaPull]: (_event, input) => proxy.requestJson("/api/ollama/models/pull", jsonInit("POST", parse(OllamaPullInputSchema, input)), OllamaPullSchema),
    [IPC_CHANNELS.ollamaGetPull]: (_event, id) => proxy.requestJson(`/api/ollama/models/pull/${parse(UUID, id)}`, {}, OllamaPullSchema),
    [IPC_CHANNELS.ollamaCancelPull]: (_event, id) => proxy.requestJson(`/api/ollama/models/pull/${parse(UUID, id)}/cancel`, jsonInit("POST"), OllamaPullSchema),
    [IPC_CHANNELS.ollamaSubscribePull]: (event, id, afterSequence) => {
      const pullId = parse(UUID, id);
      if (!Number.isInteger(afterSequence) || afterSequence < 0) throw new DocMindClientError("INVALID_REQUEST", "事件序号无效");
      return proxy.openStream({requestId: pullId, route: `/api/ollama/models/pull/${pullId}/events`, sender: event.sender ?? {send: () => {}}, afterSequence, headers: {"Last-Event-ID": String(afterSequence)}});
    },
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
    [IPC_CHANNELS.settingsSaveWebSearch]: (_event, input) =>
      proxy.requestJson(
        "/api/settings/web-search",
        jsonInit("PUT", parse(WebSearchSettingsInputSchema, input)),
        SettingsViewSchema,
      ),
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
    [IPC_CHANNELS.batchesCreate]: (_event, input) =>
      proxy.requestJson(
        "/api/import-batches",
        jsonInit("POST", parse(CreateBatchInputSchema, input)),
        BatchImportSchema,
      ),
    [IPC_CHANNELS.batchesGet]: (_event, batchId) => {
      const id = parse(UUID, batchId);
      return proxy.requestJson(`/api/import-batches/${id}`, {}, BatchImportSchema);
    },
    [IPC_CHANNELS.batchesList]: () =>
      proxy.requestJson("/api/import-batches", {}, z.array(BatchImportSchema)),
    [IPC_CHANNELS.batchesListItems]: (_event, batchId, cursor) => {
      const id = parse(UUID, batchId);
      const query =
        typeof cursor === "string" && cursor.length > 0
          ? `?cursor=${encodeURIComponent(cursor)}`
          : "";
      return proxy.requestJson(`/api/import-batches/${id}/items${query}`, {}, BatchItemPageSchema);
    },
    [IPC_CHANNELS.batchesConfirm]: (_event, batchId, input) => {
      const id = parse(UUID, batchId);
      return proxy.requestJson(
        `/api/import-batches/${id}/confirm`,
        jsonInit("POST", parse(ConfirmBatchInputSchema, input)),
        BatchImportSchema,
      );
    },
    [IPC_CHANNELS.batchesCancel]: (_event, batchId) => {
      const id = parse(UUID, batchId);
      return proxy.requestJson(
        `/api/import-batches/${id}/cancel`,
        jsonInit("POST"),
        BatchImportSchema,
      );
    },
    [IPC_CHANNELS.batchesContinue]: (_event, batchId) => {
      const id = parse(UUID, batchId);
      return proxy.requestJson(
        `/api/import-batches/${id}/continue`,
        jsonInit("POST"),
        BatchImportSchema,
      );
    },
    [IPC_CHANNELS.batchesRetry]: (_event, batchId, input) => {
      const id = parse(UUID, batchId);
      return proxy.requestJson(
        `/api/import-batches/${id}/retry`,
        jsonInit("POST", input === undefined ? undefined : parse(RetryBatchInputSchema, input)),
        BatchImportSchema,
      );
    },
    [IPC_CHANNELS.batchesSubscribe]: (event, batchId, afterSequence) => {
      const id = parse(UUID, batchId);
      if (!Number.isInteger(afterSequence) || afterSequence < 0)
        throw new DocMindClientError("INVALID_REQUEST", "事件序号无效");
      return proxy.openStream({
        requestId: id,
        afterSequence,
        route: `/api/import-batches/${id}/events`,
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
          webSearchPermission: data.webSearchPermission,
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
    [IPC_CHANNELS.chatSearchStream]: (event, input, afterSequence) => {
      const data = parse(ChatSearchInputSchema, input);
      if (afterSequence !== undefined && (!Number.isInteger(afterSequence) || afterSequence < 0))
        throw new DocMindClientError("INVALID_REQUEST", "事件序号无效");
      const options = {
        requestId: data.requestId,
        sessionId: data.sessionId,
        route: `/api/sessions/${data.sessionId}/messages/${data.userMessageId}/web-search/stream`,
        body: { requestId: data.requestId, repositoryIds: data.repositoryIds },
        sender: event.sender ?? { send: () => {} },
        ...(afterSequence === undefined
          ? {}
          : { afterSequence, headers: { "Last-Event-ID": String(afterSequence) } }),
      };
      return afterSequence === undefined ? proxy.openStream(options) : proxy.resumeStream(options);
    },
    [IPC_CHANNELS.webSearchGetRun]: (_event, runId, sessionId) =>
      proxy.requestJson(
        `/api/search/runs/${parse(UUID, runId)}?session_id=${parse(UUID, sessionId)}`,
        {},
        WebSearchRunSchema,
      ),
    [IPC_CHANNELS.webSearchCreateImportBatch]: (_event, runId, input) =>
      proxy.requestJson(
        `/api/web-search/runs/${parse(UUID, runId)}/import-batch`,
        jsonInit("POST", parse(SearchImportInputSchema, input)),
        BatchImportSchema,
      ),
    [IPC_CHANNELS.memoryEndSession]: (_event, sessionId) => {
      const id = parse(UUID, sessionId);
      return proxy.requestJson(`/api/sessions/${id}/end`, jsonInit("POST"), SessionSummarySchema);
    },
    [IPC_CHANNELS.memoryDeleteSession]: (_event, sessionId, confirm) => {
      const id = parse(UUID, sessionId);
      if (confirm !== true) throw new DocMindClientError("INVALID_REQUEST", "必须确认删除");
      return proxy.requestVoid(`/api/sessions/${id}`, jsonInit("DELETE", { confirm: true }));
    },
    [IPC_CHANNELS.memoryGetSummary]: (_event, sessionId) => {
      const id = parse(UUID, sessionId);
      return proxy.requestJson(
        `/api/sessions/${id}/summary`,
        {},
        SessionMemorySummarySchema.nullable(),
      );
    },
    [IPC_CHANNELS.memoryRegenerateSummary]: (_event, sessionId) => {
      const id = parse(UUID, sessionId);
      return proxy.requestJson(
        `/api/sessions/${id}/summary/regenerate`,
        jsonInit("POST"),
        SessionMemorySummarySchema,
      );
    },
    [IPC_CHANNELS.memoryDeleteSummary]: (_event, sessionId) => {
      const id = parse(UUID, sessionId);
      return proxy.requestVoid(`/api/sessions/${id}/summary`, jsonInit("DELETE"));
    },
    [IPC_CHANNELS.memoryCreateDistillation]: (_event, sessionId) => {
      const id = parse(UUID, sessionId);
      return proxy.requestJson(
        `/api/sessions/${id}/distillations`,
        jsonInit("POST"),
        DistillationViewSchema,
      );
    },
    [IPC_CHANNELS.memoryGetDistillation]: (_event, distillationId) => {
      const id = parse(UUID, distillationId);
      return proxy.requestJson(`/api/distillations/${id}`, {}, DistillationViewSchema);
    },
    [IPC_CHANNELS.memoryUpdateDistillation]: (_event, distillationId, input) => {
      const id = parse(UUID, distillationId);
      return proxy.requestJson(
        `/api/distillations/${id}`,
        jsonInit("PUT", parse(DistillationEditSchema, input)),
        DistillationViewSchema,
      );
    },
    [IPC_CHANNELS.memoryRegenerateDistillation]: (_event, distillationId) => {
      const id = parse(UUID, distillationId);
      return proxy.requestJson(
        `/api/distillations/${id}/regenerate`,
        jsonInit("POST"),
        DistillationViewSchema,
      );
    },
    [IPC_CHANNELS.memorySaveDistillation]: (_event, distillationId, input) => {
      const id = parse(UUID, distillationId);
      return proxy.requestJson(
        `/api/distillations/${id}/save`,
        jsonInit("POST", parse(DistillationTargetSchema, input)),
        DistillationViewSchema,
      );
    },
    [IPC_CHANNELS.memoryDeleteDistillation]: (_event, distillationId) => {
      const id = parse(UUID, distillationId);
      return proxy.requestVoid(`/api/distillations/${id}`, jsonInit("DELETE"));
    },
    [IPC_CHANNELS.memoryList]: (_event, input) => {
      const data = parse(MemoryListInputSchema, input);
      const query = new URLSearchParams({ repositoryIds: data.repositoryIds.join(",") });
      if (data.kind) query.set("kind", data.kind);
      if (data.cursor) query.set("cursor", data.cursor);
      return proxy.requestJson(`/api/memories?${query}`, {}, MemoryItemPageSchema);
    },
    [IPC_CHANNELS.memorySubscribeDistillation]: (event, distillationId, afterSequence) => {
      const id = parse(UUID, distillationId);
      if (!Number.isInteger(afterSequence) || afterSequence < 0)
        throw new DocMindClientError("INVALID_REQUEST", "事件序号无效");
      return proxy.openStream({
        requestId: id,
        afterSequence,
        route: `/api/distillations/${id}/events`,
        headers: { "Last-Event-ID": String(afterSequence) },
        sender: event.sender ?? { send: () => {} },
      });
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
    [IPC_CHANNELS.sourcesStageDirectory]: (_event, ...args) => {
      if (args.length !== 0) throw new DocMindClientError("INVALID_REQUEST", "请求参数无效");
      return stagedFiles
        .stageDirectory()
        .then((value) => (value === null ? null : parse(StagedCollectionSchema, value)))
        .catch((error: unknown) => {
          if (error instanceof DocMindClientError) throw error;
          const candidate = error as { code?: unknown; message?: unknown };
          if (typeof candidate.code === "string")
            throw new DocMindClientError(
              candidate.code,
              typeof candidate.message === "string" ? candidate.message : "目录操作失败",
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
        channel === IPC_CHANNELS.chatStream ||
        channel === IPC_CHANNELS.chatSearchStream ||
        channel === IPC_CHANNELS.memorySubscribeDistillation
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
    BATCH_LIMIT_EXCEEDED: "目录超过批量导入限制",
    BATCH_SOURCE_CHANGED: "目录内容在暂存期间发生变化",
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
  const requestId =
    channel === IPC_CHANNELS.chatStream || channel === IPC_CHANNELS.chatSearchStream
      ? args[0]?.requestId
      : args[0];
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
