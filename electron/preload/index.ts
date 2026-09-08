import { contextBridge, ipcRenderer } from "electron";
import { z } from "zod";
import { IPC_CHANNELS, streamEventChannel } from "../shared/channels";
import {
  ChatStreamInputSchema,
  ConflictResolutionSchema,
  ConflictViewSchema,
  CreateImportInputSchema,
  CreateRepositoryInputSchema,
  CreateSessionInputSchema,
  DocumentDetailSchema,
  DocumentVersionSchema,
  DocumentInputSchema,
  DocumentSummarySchema,
  EventEnvelopeSchema,
  GraphEdgeSchema,
  GraphNodeSchema,
  ImportJobSchema,
  IpcResultSchema,
  MessageSchema,
  ModelConnectionResultSchema,
  ModelSettingsInputSchema,
  ModelStatusSchema,
  RepositorySchema,
  SyncOutcomeSchema,
  SyncStatusSchema,
  SessionSummarySchema,
  SettingsViewSchema,
  SourcePreviewSchema,
  SourceRefSchema,
  StagedCollectionSchema,
  StagedSourceSchema,
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
  OllamaStatusSchema,
  OllamaModelsSchema,
  OllamaPullInputSchema,
  OllamaPullSchema,
  RuntimeSettingsInputSchema,
} from "../shared/contracts";
import type { DocMindApi, EventEnvelope } from "../shared/contracts";

const uuid = (value: unknown): string => {
  if (
    typeof value !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
  )
    throw new Error("INVALID_REQUEST");
  return value;
};

async function invoke<T extends z.ZodTypeAny>(
  channel: string,
  schema: T,
  ...args: unknown[]
): Promise<z.output<T>> {
  const result = IpcResultSchema(schema).parse(await ipcRenderer.invoke(channel, ...args));
  if ("error" in result) throw result.error;
  return result.value as z.output<T>;
}

function subscription(
  channel: string,
  requestId: string,
  startArgs: unknown[],
  onEvent: (event: EventEnvelope) => void,
  afterSequence?: number,
) {
  const eventChannel = streamEventChannel(requestId);
  const key = `${channel}:${requestId}`;
  const previous = activeSubscriptions.get(key);
  previous?.cancel();
  let attached = true;
  let cancelled = false;
  let lastSequence = -1;
  const listener = (_event: unknown, payload: unknown) => {
    const parsed = EventEnvelopeSchema.safeParse(payload);
    if (parsed.success && parsed.data.sequence > lastSequence) {
      lastSequence = parsed.data.sequence;
      const terminal = parsed.data.type === "done" || parsed.data.type === "error";
      if (terminal) {
        ipcRenderer.removeListener(eventChannel, listener);
        attached = false;
        activeSubscriptions.delete(key);
      }
      onEvent(parsed.data);
    }
  };
  ipcRenderer.on(eventChannel, listener);
  ipcRenderer.send(channel, ...startArgs, afterSequence);
  const detach = () => {
    if (!attached) return;
    attached = false;
    ipcRenderer.removeListener(eventChannel, listener);
    activeSubscriptions.delete(key);
  };
  const result = {
    requestId,
    detach,
    cancel: () => {
      if (cancelled) return;
      cancelled = true;
      detach();
      activeSubscriptions.delete(key);
      ipcRenderer.send(IPC_CHANNELS.streamCancel, requestId);
    },
  };
  activeSubscriptions.set(key, result);
  return result;
}

const activeSubscriptions = new Map<string, { cancel(): void }>();

const api: DocMindApi = {
  ollama: {
    status: () => invoke(IPC_CHANNELS.ollamaStatus, OllamaStatusSchema),
    models: () => invoke(IPC_CHANNELS.ollamaModels, OllamaModelsSchema),
    pull: (input) =>
      invoke(IPC_CHANNELS.ollamaPull, OllamaPullSchema, OllamaPullInputSchema.parse(input)),
    getPull: (id) => invoke(IPC_CHANNELS.ollamaGetPull, OllamaPullSchema, uuid(id)),
    cancelPull: (id) => invoke(IPC_CHANNELS.ollamaCancelPull, OllamaPullSchema, uuid(id)),
    retryPull: (id) => invoke(IPC_CHANNELS.ollamaRetryPull, OllamaPullSchema, uuid(id)),
    subscribePull: (id, afterSequence, onEvent) => {
      const value = uuid(id);
      if (!Number.isInteger(afterSequence) || afterSequence < 0) throw new Error("INVALID_REQUEST");
      return subscription(IPC_CHANNELS.ollamaSubscribePull, value, [value], onEvent, afterSequence);
    },
  },
  settings: {
    get: () => invoke(IPC_CHANNELS.settingsGet, SettingsViewSchema),
    saveModel: (input) =>
      invoke(
        IPC_CHANNELS.settingsSaveModel,
        SettingsViewSchema,
        ModelSettingsInputSchema.parse(input),
      ),
    testModel: () => invoke(IPC_CHANNELS.settingsTestModel, ModelConnectionResultSchema),
    clearDiagnostics: () => invoke(IPC_CHANNELS.settingsClearDiagnostics, z.undefined()),
    saveWebSearch: (input) =>
      invoke(
        IPC_CHANNELS.settingsSaveWebSearch,
        SettingsViewSchema,
        WebSearchSettingsInputSchema.parse(input),
      ),
    saveRuntime: (input) =>
      invoke(
        IPC_CHANNELS.settingsSaveRuntime,
        SettingsViewSchema,
        RuntimeSettingsInputSchema.parse(input),
      ),
  },
  embedding: {
    status: () => invoke(IPC_CHANNELS.embeddingStatus, ModelStatusSchema),
    prepare: () => invoke(IPC_CHANNELS.embeddingPrepare, ModelStatusSchema),
  },
  yuque: {
    status: () => invoke(IPC_CHANNELS.yuqueStatus, YuqueStatusSchema),
    login: () => invoke(IPC_CHANNELS.yuqueLogin, YuqueStatusSchema),
  },
  repositories: {
    list: () => invoke(IPC_CHANNELS.repositoriesList, RepositorySchema.array()),
    create: (input) =>
      invoke(
        IPC_CHANNELS.repositoriesCreate,
        RepositorySchema,
        CreateRepositoryInputSchema.parse(input),
      ),
  },
  sync: {
    get: (repositoryId) =>
      invoke(IPC_CHANNELS.syncGet, SyncStatusSchema, uuid(repositoryId)),
    trigger: (repositoryId) =>
      invoke(IPC_CHANNELS.syncTrigger, SyncOutcomeSchema, uuid(repositoryId)),
  },
  conflicts: {
    list: (repositoryId) =>
      invoke(IPC_CHANNELS.conflictsList, ConflictViewSchema.array(), uuid(repositoryId)),
    resolve: (documentId, resolution) =>
      invoke(
        IPC_CHANNELS.conflictsResolve,
        z.undefined(),
        uuid(documentId),
        ConflictResolutionSchema.parse(resolution),
      ),
  },
  versions: {
    list: (documentId) =>
      invoke(IPC_CHANNELS.versionsList, DocumentVersionSchema.array(), uuid(documentId)),
  },
  graph: {
    nodes: () => invoke(IPC_CHANNELS.graphNodes, GraphNodeSchema.array()),
    adjacency: (nodeId) =>
      invoke(IPC_CHANNELS.graphAdjacency, GraphEdgeSchema.array(), String(nodeId)),
  },
  documents: {
    list: (repositoryId) =>
      invoke(IPC_CHANNELS.documentsList, DocumentSummarySchema.array(), uuid(repositoryId)),
    read: (documentId) =>
      invoke(IPC_CHANNELS.documentsRead, DocumentDetailSchema, uuid(documentId)),
    create: (repositoryId, input) =>
      invoke(
        IPC_CHANNELS.documentsCreate,
        DocumentDetailSchema,
        uuid(repositoryId),
        DocumentInputSchema.parse(input),
      ),
    update: (documentId, input) =>
      invoke(
        IPC_CHANNELS.documentsUpdate,
        DocumentDetailSchema,
        uuid(documentId),
        DocumentInputSchema.parse(input),
      ),
    delete: (documentId, confirm) => {
      if (confirm !== true) return Promise.reject(new Error("INVALID_REQUEST"));
      return invoke(IPC_CHANNELS.documentsDelete, z.undefined(), uuid(documentId), true);
    },
  },
  imports: {
    inspect: (input) =>
      invoke(IPC_CHANNELS.importsInspect, SourcePreviewSchema, SourceRefSchema.parse(input)),
    create: (input) =>
      invoke(IPC_CHANNELS.importsCreate, ImportJobSchema, CreateImportInputSchema.parse(input)),
    get: (jobId) => invoke(IPC_CHANNELS.importsGet, ImportJobSchema, uuid(jobId)),
    retry: (jobId) => invoke(IPC_CHANNELS.importsRetry, ImportJobSchema, uuid(jobId)),
    cancel: (jobId) => invoke(IPC_CHANNELS.importsCancel, ImportJobSchema, uuid(jobId)),
    subscribe: (jobId, afterSequence, onEvent) => {
      const id = uuid(jobId);
      if (!Number.isInteger(afterSequence) || afterSequence < 0) throw new Error("INVALID_REQUEST");
      return subscription(IPC_CHANNELS.importsSubscribe, id, [id, afterSequence], onEvent);
    },
  },
  chat: {
    listSessions: () => invoke(IPC_CHANNELS.chatListSessions, SessionSummarySchema.array()),
    createSession: (input) =>
      invoke(
        IPC_CHANNELS.chatCreateSession,
        SessionSummarySchema,
        CreateSessionInputSchema.parse(input),
      ),
    listMessages: (sessionId) =>
      invoke(IPC_CHANNELS.chatListMessages, MessageSchema.array(), uuid(sessionId)),
    stream: (input, onEvent, afterSequence) => {
      const data = ChatStreamInputSchema.parse(input);
      if (afterSequence !== undefined && (!Number.isInteger(afterSequence) || afterSequence < 0))
        throw new Error("INVALID_REQUEST");
      return subscription(IPC_CHANNELS.chatStream, data.requestId, [data], onEvent, afterSequence);
    },
    searchStream: (input, onEvent, afterSequence) => {
      const data = ChatSearchInputSchema.parse(input);
      if (afterSequence !== undefined && (!Number.isInteger(afterSequence) || afterSequence < 0))
        throw new Error("INVALID_REQUEST");
      return subscription(
        IPC_CHANNELS.chatSearchStream,
        data.requestId,
        [data],
        onEvent,
        afterSequence,
      );
    },
  },
  webSearch: {
    getRun: (runId, sessionId) =>
      invoke(IPC_CHANNELS.webSearchGetRun, WebSearchRunSchema, uuid(runId), uuid(sessionId)),
    createImportBatch: (runId, input) =>
      invoke(
        IPC_CHANNELS.webSearchCreateImportBatch,
        BatchImportSchema,
        uuid(runId),
        SearchImportInputSchema.parse(input),
      ),
  },
  memory: {
    endSession: (sessionId) =>
      invoke(IPC_CHANNELS.memoryEndSession, SessionSummarySchema, uuid(sessionId)),
    deleteSession: (sessionId, confirm) => {
      if (confirm !== true) return Promise.reject(new Error("INVALID_REQUEST"));
      return invoke(IPC_CHANNELS.memoryDeleteSession, z.undefined(), uuid(sessionId), true);
    },
    getSummary: (sessionId) =>
      invoke(IPC_CHANNELS.memoryGetSummary, SessionMemorySummarySchema.nullable(), uuid(sessionId)),
    regenerateSummary: (sessionId) =>
      invoke(IPC_CHANNELS.memoryRegenerateSummary, SessionMemorySummarySchema, uuid(sessionId)),
    deleteSummary: (sessionId) =>
      invoke(IPC_CHANNELS.memoryDeleteSummary, z.undefined(), uuid(sessionId)),
    createDistillation: (sessionId) =>
      invoke(IPC_CHANNELS.memoryCreateDistillation, DistillationViewSchema, uuid(sessionId)),
    getDistillation: (distillationId) =>
      invoke(IPC_CHANNELS.memoryGetDistillation, DistillationViewSchema, uuid(distillationId)),
    updateDistillation: (distillationId, input) =>
      invoke(
        IPC_CHANNELS.memoryUpdateDistillation,
        DistillationViewSchema,
        uuid(distillationId),
        DistillationEditSchema.parse(input),
      ),
    regenerateDistillation: (distillationId) =>
      invoke(
        IPC_CHANNELS.memoryRegenerateDistillation,
        DistillationViewSchema,
        uuid(distillationId),
      ),
    saveDistillation: (distillationId, input) =>
      invoke(
        IPC_CHANNELS.memorySaveDistillation,
        DistillationViewSchema,
        uuid(distillationId),
        DistillationTargetSchema.parse(input),
      ),
    deleteDistillation: (distillationId) =>
      invoke(IPC_CHANNELS.memoryDeleteDistillation, z.undefined(), uuid(distillationId)),
    list: (input) =>
      invoke(IPC_CHANNELS.memoryList, MemoryItemPageSchema, MemoryListInputSchema.parse(input)),
    subscribeDistillation: (distillationId, afterSequence, onEvent) => {
      const id = uuid(distillationId);
      if (!Number.isInteger(afterSequence) || afterSequence < 0) throw new Error("INVALID_REQUEST");
      return subscription(
        IPC_CHANNELS.memorySubscribeDistillation,
        id,
        [id],
        onEvent,
        afterSequence,
      );
    },
  },
  dialogs: {
    chooseSource: (kind) => {
      if (kind !== "pdf" && kind !== "markdown")
        return Promise.reject(new Error("INVALID_REQUEST"));
      return invoke(IPC_CHANNELS.dialogsChooseSource, StagedSourceSchema.nullable(), kind);
    },
  },
  sources: {
    stageDirectory: () =>
      invoke(IPC_CHANNELS.sourcesStageDirectory, StagedCollectionSchema.nullable()),
  },
  batches: {
    create: (input) =>
      invoke(IPC_CHANNELS.batchesCreate, BatchImportSchema, CreateBatchInputSchema.parse(input)),
    get: (batchId) => invoke(IPC_CHANNELS.batchesGet, BatchImportSchema, uuid(batchId)),
    list: () => invoke(IPC_CHANNELS.batchesList, BatchImportSchema.array()),
    listItems: (batchId, cursor) =>
      invoke(IPC_CHANNELS.batchesListItems, BatchItemPageSchema, uuid(batchId), cursor ?? null),
    confirm: (batchId, input) =>
      invoke(
        IPC_CHANNELS.batchesConfirm,
        BatchImportSchema,
        uuid(batchId),
        ConfirmBatchInputSchema.parse(input),
      ),
    cancel: (batchId) => invoke(IPC_CHANNELS.batchesCancel, BatchImportSchema, uuid(batchId)),
    continue: (batchId) => invoke(IPC_CHANNELS.batchesContinue, BatchImportSchema, uuid(batchId)),
    retry: (batchId, input) =>
      invoke(
        IPC_CHANNELS.batchesRetry,
        BatchImportSchema,
        uuid(batchId),
        input ? RetryBatchInputSchema.parse(input) : undefined,
      ),
    subscribe: (batchId, afterSequence, onEvent) => {
      const id = uuid(batchId);
      if (!Number.isInteger(afterSequence) || afterSequence < 0) throw new Error("INVALID_REQUEST");
      return subscription(IPC_CHANNELS.batchesSubscribe, id, [id, afterSequence], onEvent);
    },
  },
  shell: {
    openExternal: (url) => {
      let parsed: URL;
      try {
        parsed = new URL(url);
      } catch {
        return Promise.reject(new Error("INVALID_REQUEST"));
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:")
        return Promise.reject(new Error("INVALID_REQUEST"));
      return invoke(IPC_CHANNELS.shellOpenExternal, z.undefined(), parsed.toString());
    },
  },
};

for (const value of Object.values(api)) Object.freeze(value);
Object.freeze(api);
contextBridge.exposeInMainWorld("docmind", api);
