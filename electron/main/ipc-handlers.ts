import { createRequire } from "node:module";
import { z } from "zod";
import { BackendProxy, DocMindClientError, type StreamSender } from "./backend-proxy";
import { StagedFileService } from "./staged-files";
import { redactSecrets } from "./redaction";
import { IPC_CHANNELS, PUSH_CHANNELS, streamEventChannel } from "../shared/channels";
import {
  ChatStreamInputSchema,
  ConflictResolutionSchema,
  ConflictViewSchema,
  CreateImportInputSchema,
  CreateRepositoryInputSchema,
  UpdateRepositoryInputSchema,
  CreateSessionInputSchema,
  DocumentDetailSchema,
  DocumentVersionSchema,
  DocumentInputSchema,
  DocumentSummarySchema,
  ErrorEnvelopeSchema,
  GraphEdgeSchema,
  GraphNodeSchema,
  ImportJobSchema,
  MessageSchema,
  ModelConnectionResultSchema,
  ModelListProbeSchema,
  ModelListViewSchema,
  ModelSettingsInputSchema,
  ModelStatusSchema,
  RepositorySchema,
  SyncOutcomeSchema,
  SyncStatusSchema,
  SessionSummarySchema,
  SettingsViewSchema,
  SourcePreviewSchema,
  SourceRefSchema,
  StagedSourceSchema,
  StagedCollectionSchema,
  SourceFormatSchema,
  RemoteStatusSchema,
  RemoteProviderSummarySchema,
  RemoteCredentialChannelSchema,
  RemoteCredentialTestResultSchema,
  SaveRemoteCredentialInputSchema,
  PluginManifestSchema,
  PluginDiagnosticSchema,
  PluginDirectorySchema,
  PluginStateSchema,
  BatchImportSchema,
  BatchItemPageSchema,
  BrowserInstallResultSchema,
  CreateBatchInputSchema,
  ConfirmBatchInputSchema,
  RetryBatchInputSchema,
  DistillationEditSchema,
  DistillationTargetSchema,
  DistillationViewSchema,
  MemoryItemPageSchema,
  MemoryListInputSchema,
  SessionMemorySummarySchema,
  WebSearchRunSchema,
  SearchImportInputSchema,
  ChatSearchInputSchema,
  LocalModelStatusSchema,
  LocalModelsSchema,
  RuntimeSettingsInputSchema,
} from "../shared/contracts";

const require = createRequire(import.meta.url);
let electronIpc: {
  ipcMain?: any;
  app?: {
    on?(event: "before-quit", listener: () => void): void;
    relaunch?(): void;
    quit?(): void;
  };
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
  app?: {
    on?(event: "before-quit", listener: () => void): void;
    relaunch?(): void;
    quit?(): void;
    exit?(code: number): void;
  };
}

export type IpcHandlerMap = Record<string, Handler>;

const UUID = z.string().uuid();
/**
 * 原件分片的字节偏移。上限 2^53-1 由 z 的 int 校验覆盖，这里再压到
 * Number.MAX_SAFE_INTEGER 以下，避免拼接进 Range 头时出现科学计数法。
 */
const BYTE_OFFSET = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
/**
 * A plugin search box must not be able to smuggle anything into the query
 * string. Bounded to the same length the backend accepts, and encoded on the
 * way out; the value is user input, so it is validated like any other.
 */
const PLUGIN_QUERY = z.string().trim().max(200);
/**
 * A plugin's name, and nothing else.
 *
 * The request never carries a path: the server resolves the name against the
 * plugins discovery found and takes the directory from its own scan. This
 * pattern is the client-side half of that — a name that could be a path never
 * leaves this process, so a bug in the server's resolution cannot be reached
 * from a compromised renderer either.
 */
const PLUGIN_NAME = z
  .string()
  .trim()
  .min(1)
  .max(120)
  .regex(/^[A-Za-z0-9._-]+$/);
const REMOTE_PROVIDER = z
  .string()
  .trim()
  .min(1)
  .max(64)
  .regex(/^[A-Za-z0-9_-]+$/);
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
    [IPC_CHANNELS.localModelStatus]: () =>
      proxy.requestJson("/api/local-model/status", {}, LocalModelStatusSchema),
    [IPC_CHANNELS.localModelModels]: () =>
      proxy.requestJson("/api/local-model/models", {}, LocalModelsSchema),
    [IPC_CHANNELS.settingsGet]: () => proxy.requestJson("/api/settings", {}, SettingsViewSchema),
    [IPC_CHANNELS.settingsSaveModel]: (_event, input) =>
      proxy.requestJson(
        "/api/settings/model",
        jsonInit("PUT", parse(ModelSettingsInputSchema, input)),
        SettingsViewSchema,
      ),
    [IPC_CHANNELS.settingsTestModel]: () =>
      proxy.requestJson("/api/settings/model/test", jsonInit("POST"), ModelConnectionResultSchema),
    [IPC_CHANNELS.settingsListModels]: (_event, input) =>
      proxy.requestJson(
        "/api/settings/model/list",
        jsonInit("POST", parse(ModelListProbeSchema, input ?? {})),
        ModelListViewSchema,
      ),
    [IPC_CHANNELS.settingsSkipModelSetup]: () =>
      proxy.requestJson(
        "/api/settings/model/skip-setup",
        jsonInit("POST"),
        SettingsViewSchema,
      ),
    [IPC_CHANNELS.settingsClearDiagnostics]: () =>
      proxy.requestVoid("/api/settings/diagnostics/clear", jsonInit("POST")),
    [IPC_CHANNELS.settingsSaveRuntime]: (_event, input) =>
      proxy.requestJson(
        "/api/settings/runtime",
        jsonInit("POST", parse(RuntimeSettingsInputSchema, input)),
        SettingsViewSchema,
      ),
    [IPC_CHANNELS.embeddingStatus]: () =>
      proxy.requestJson("/api/embedding/status", {}, ModelStatusSchema),
    [IPC_CHANNELS.embeddingPrepare]: () =>
      proxy.requestJson("/api/embedding/prepare", jsonInit("POST"), ModelStatusSchema),
    [IPC_CHANNELS.remoteStatus]: (_event, provider) =>
      proxy.requestJson(
        `/api/remote/providers/${encodeURIComponent(parse(REMOTE_PROVIDER, provider))}/status`,
        {},
        RemoteStatusSchema,
      ),
    [IPC_CHANNELS.remoteLogin]: (_event, provider) =>
      proxy.requestJson(
        `/api/remote/providers/${encodeURIComponent(parse(REMOTE_PROVIDER, provider))}/login`,
        jsonInit("POST"),
        RemoteStatusSchema,
      ),
    [IPC_CHANNELS.remoteInstallBrowser]: (_event, provider) =>
      proxy.requestJson(
        `/api/remote/providers/${encodeURIComponent(
          parse(REMOTE_PROVIDER, provider),
        )}/browser/install`,
        jsonInit("POST"),
        BrowserInstallResultSchema,
      ),
    [IPC_CHANNELS.remoteListProviders]: () =>
      proxy.requestJson(
        "/api/remote/providers",
        {},
        z.array(RemoteProviderSummarySchema),
      ),
    [IPC_CHANNELS.remoteListCredentials]: (_event, provider) =>
      proxy.requestJson(
        `/api/remote/providers/${encodeURIComponent(
          parse(REMOTE_PROVIDER, provider),
        )}/credentials`,
        {},
        z.array(RemoteCredentialChannelSchema),
      ),
    [IPC_CHANNELS.remoteSaveCredential]: (_event, provider, channel, input) =>
      proxy.requestJson(
        `/api/remote/providers/${encodeURIComponent(
          parse(REMOTE_PROVIDER, provider),
        )}/credentials/${encodeURIComponent(parse(REMOTE_PROVIDER, channel))}`,
        jsonInit("PUT", parse(SaveRemoteCredentialInputSchema, input)),
        RemoteCredentialChannelSchema,
      ),
    [IPC_CHANNELS.remoteTestCredential]: (_event, provider, channel) =>
      proxy.requestJson(
        `/api/remote/providers/${encodeURIComponent(
          parse(REMOTE_PROVIDER, provider),
        )}/credentials/${encodeURIComponent(parse(REMOTE_PROVIDER, channel))}/test`,
        jsonInit("POST"),
        RemoteCredentialTestResultSchema,
      ),
    [IPC_CHANNELS.remoteDeleteCredential]: (_event, provider, channel) =>
      proxy.requestJson(
        `/api/remote/providers/${encodeURIComponent(
          parse(REMOTE_PROVIDER, provider),
        )}/credentials/${encodeURIComponent(parse(REMOTE_PROVIDER, channel))}`,
        jsonInit("DELETE"),
        RemoteCredentialChannelSchema,
      ),
    [IPC_CHANNELS.pluginsList]: (_event, query) =>
      proxy.requestJson(
        `/api/plugins${query ? `?q=${encodeURIComponent(parse(PLUGIN_QUERY, query))}` : ""}`,
        {},
        z.array(PluginManifestSchema),
      ),
    [IPC_CHANNELS.pluginsDiagnostics]: () =>
      proxy.requestJson("/api/plugins/diagnostics", {}, z.array(PluginDiagnosticSchema)),
    [IPC_CHANNELS.pluginsDirectory]: () =>
      proxy.requestJson("/api/plugins/directory", {}, PluginDirectorySchema),
    [IPC_CHANNELS.pluginsSetEnabled]: (_event, plugin, enabled) =>
      proxy.requestJson(
        `/api/plugins/${encodeURIComponent(parse(PLUGIN_NAME, plugin))}/enabled`,
        jsonInit("PUT", { enabled: parse(z.boolean(), enabled) }),
        PluginStateSchema,
      ),
    [IPC_CHANNELS.pluginsUninstall]: (_event, plugin) =>
      proxy.requestJson(
        `/api/plugins/${encodeURIComponent(parse(PLUGIN_NAME, plugin))}`,
        jsonInit("DELETE"),
        PluginStateSchema,
      ),
    [IPC_CHANNELS.repositoriesList]: () =>
      proxy.requestJson("/api/repositories", {}, z.array(RepositorySchema)),
    [IPC_CHANNELS.repositoriesCreate]: (_event, input) =>
      proxy.requestJson(
        "/api/repositories",
        jsonInit("POST", parse(CreateRepositoryInputSchema, input)),
        RepositorySchema,
      ),
    [IPC_CHANNELS.repositoriesUpdate]: (_event, repositoryId, input) =>
      proxy.requestJson(
        `/api/repositories/${parse(UUID, repositoryId)}`,
        jsonInit("PATCH", parse(UpdateRepositoryInputSchema, input)),
        RepositorySchema,
      ),
    [IPC_CHANNELS.syncGet]: (_event, repositoryId) =>
      proxy.requestJson(
        `/api/repositories/${parse(UUID, repositoryId)}/sync`,
        {},
        SyncStatusSchema,
      ),
    [IPC_CHANNELS.syncTrigger]: (_event, repositoryId) =>
      proxy.requestJson(
        `/api/repositories/${parse(UUID, repositoryId)}/sync`,
        jsonInit("POST"),
        SyncOutcomeSchema,
      ),
    [IPC_CHANNELS.conflictsList]: (_event, repositoryId) =>
      proxy.requestJson(
        `/api/repositories/${parse(UUID, repositoryId)}/conflicts`,
        {},
        z.array(ConflictViewSchema),
      ),
    [IPC_CHANNELS.conflictsResolve]: (_event, documentId, resolution) =>
      proxy.requestVoid(
        `/api/documents/${parse(UUID, documentId)}/conflict`,
        jsonInit("POST", parse(ConflictResolutionSchema, resolution)),
      ),
    [IPC_CHANNELS.versionsList]: (_event, documentId) =>
      proxy.requestJson(
        `/api/documents/${parse(UUID, documentId)}/versions`,
        {},
        z.array(DocumentVersionSchema),
      ),
    [IPC_CHANNELS.graphNodes]: () =>
      proxy.requestJson("/api/graph/nodes", {}, z.array(GraphNodeSchema)),
    [IPC_CHANNELS.graphAdjacency]: (_event, nodeId) =>
      proxy.requestJson(
        `/api/graph/nodes/${encodeURIComponent(String(nodeId))}/adjacency`,
        {},
        z.array(GraphEdgeSchema),
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
    /**
     * 原件按需分片。阅读器自己不持有后端地址与令牌，所有字节都必须经这里转发；
     * 以 base64 回传是因为 contextBridge 不保证透传 TypedArray。
     */
    [IPC_CHANNELS.documentsReadOriginalChunk]: async (_event, documentId, begin, end) => {
      const id = parse(UUID, documentId);
      const start = parse(BYTE_OFFSET, begin);
      const stop = parse(BYTE_OFFSET, end);
      if (stop < start) throw new DocMindClientError("INVALID_REQUEST", "请求参数无效");
      const { data, total } = await proxy.requestRange(`/api/documents/${id}/raw`, start, stop);
      return { data: Buffer.from(data).toString("base64"), total };
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
    [IPC_CHANNELS.sourcesListFormats]: () =>
      proxy.requestJson("/api/imports/formats", {}, SourceFormatSchema.array()),
    [IPC_CHANNELS.dialogsChooseSource]: (_event, name) => {
      // Arity and shape are checked before any await, so a malformed call
      // fails here rather than after a needless backend round trip.
      if (typeof name !== "string" || name.length === 0 || name.length > 64)
        throw new DocMindClientError("INVALID_REQUEST", "来源类型无效");
      // The format list lives in the backend, so a picker opened for a format
      // a plugin contributed works without this process knowing it exists.
      return proxy
        .requestJson("/api/imports/formats", {}, SourceFormatSchema.array())
        .then((formats) => {
          const format = formats.find((candidate) => candidate.name === name);
          if (!format) throw new DocMindClientError("INVALID_REQUEST", "来源类型无效");
          return stagedFiles.chooseAndStage(format);
        })
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
      return proxy
        .requestJson("/api/imports/formats", {}, SourceFormatSchema.array())
        .then((formats) => stagedFiles.stageDirectory(formats))
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
    /**
     * Restart is the only real escape hatch when the renderer and the backend
     * disagree on the settings contract: refetching re-runs the same failing
     * validation, so the user would be stuck in a retry loop. `relaunch()` only
     * takes effect on exit, and `quit()` still routes through `before-quit`,
     * where index.ts stops the backend first — so the old runtime never keeps
     * holding port 18900 against the new instance.
     */
    [IPC_CHANNELS.appRestart]: () => {
      const relaunch = dependencies.app?.relaunch ?? electronIpc.app?.relaunch;
      const quit = dependencies.app?.quit ?? electronIpc.app?.quit;
      if (!relaunch || !quit) throw new DocMindClientError("APP_RESTART_UNAVAILABLE", "无法重启应用");
      relaunch.call(dependencies.app ?? electronIpc.app);
      quit.call(dependencies.app ?? electronIpc.app);
    },
    [IPC_CHANNELS.streamCancel]: (_event, requestId) => {
      const id = parse(UUID, requestId);
      proxy.cancel(id);
    },
  };

  const ipcMain = dependencies.ipcMain ?? electronIpc.ipcMain;
  if (ipcMain) {
    for (const [channel, handler] of Object.entries(handlers)) {
      if (PUSH_CHANNELS.has(channel)) {
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
  const clean = (text: string) => redactSecrets(text);
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
