import { contextBridge, ipcRenderer } from "electron";
import { IPC_CHANNELS, streamEventChannel } from "../shared/channels";
import {
  ChatStreamInputSchema,
  CreateImportInputSchema,
  CreateRepositoryInputSchema,
  CreateSessionInputSchema,
  DocumentDetailSchema,
  DocumentInputSchema,
  DocumentSummarySchema,
  EventEnvelopeSchema,
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
import type { DocMindApi, EventEnvelope } from "../shared/contracts";

const uuid = (value: unknown): string => {
  if (typeof value !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) throw new Error("INVALID_REQUEST");
  return value;
};

async function invoke<T>(channel: string, schema: { parse(value: unknown): T }, ...args: unknown[]): Promise<T> {
  return schema.parse(await ipcRenderer.invoke(channel, ...args));
}

function subscription(channel: string, requestId: string, startArgs: unknown[], onEvent: (event: EventEnvelope) => void) {
  const eventChannel = streamEventChannel(requestId);
  const key = `${channel}:${requestId}`;
  const previous = activeSubscriptions.get(key);
  previous?.cancel();
  let active = true;
  let lastSequence = -1;
  const listener = (_event: unknown, payload: unknown) => {
    const parsed = EventEnvelopeSchema.safeParse(payload);
    if (parsed.success && parsed.data.sequence > lastSequence) {
      lastSequence = parsed.data.sequence;
      onEvent(parsed.data);
      if (parsed.data.type === "done" || parsed.data.type === "error") {
        active = false;
        ipcRenderer.removeListener(eventChannel, listener);
        activeSubscriptions.delete(key);
      }
    }
  };
  ipcRenderer.on(eventChannel, listener);
  ipcRenderer.send(channel, ...startArgs);
  const result = {
    requestId,
    cancel: () => {
      if (!active) return;
      active = false;
      ipcRenderer.removeListener(eventChannel, listener);
      ipcRenderer.send(IPC_CHANNELS.streamCancel, requestId);
      activeSubscriptions.delete(key);
    },
  };
  activeSubscriptions.set(key, result);
  return result;
}

const activeSubscriptions = new Map<string, { cancel(): void }>();

const api: DocMindApi = {
  settings: {
    get: () => invoke(IPC_CHANNELS.settingsGet, SettingsViewSchema),
    saveModel: (input) => invoke(IPC_CHANNELS.settingsSaveModel, SettingsViewSchema, ModelSettingsInputSchema.parse(input)),
    testModel: () => invoke(IPC_CHANNELS.settingsTestModel, ModelConnectionResultSchema),
    clearDiagnostics: () => ipcRenderer.invoke(IPC_CHANNELS.settingsClearDiagnostics).then(() => undefined),
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
    create: (input) => invoke(IPC_CHANNELS.repositoriesCreate, RepositorySchema, CreateRepositoryInputSchema.parse(input)),
  },
  documents: {
    list: (repositoryId) => invoke(IPC_CHANNELS.documentsList, DocumentSummarySchema.array(), uuid(repositoryId)),
    read: (documentId) => invoke(IPC_CHANNELS.documentsRead, DocumentDetailSchema, uuid(documentId)),
    create: (repositoryId, input) => invoke(IPC_CHANNELS.documentsCreate, DocumentDetailSchema, uuid(repositoryId), DocumentInputSchema.parse(input)),
    update: (documentId, input) => invoke(IPC_CHANNELS.documentsUpdate, DocumentDetailSchema, uuid(documentId), DocumentInputSchema.parse(input)),
    delete: (documentId, confirm) => {
      if (confirm !== true) return Promise.reject(new Error("INVALID_REQUEST"));
      return ipcRenderer.invoke(IPC_CHANNELS.documentsDelete, uuid(documentId), true).then(() => undefined);
    },
  },
  imports: {
    inspect: (input) => invoke(IPC_CHANNELS.importsInspect, SourcePreviewSchema, SourceRefSchema.parse(input)),
    create: (input) => invoke(IPC_CHANNELS.importsCreate, ImportJobSchema, CreateImportInputSchema.parse(input)),
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
    createSession: (input) => invoke(IPC_CHANNELS.chatCreateSession, SessionSummarySchema, CreateSessionInputSchema.parse(input)),
    listMessages: (sessionId) => invoke(IPC_CHANNELS.chatListMessages, MessageSchema.array(), uuid(sessionId)),
    stream: (input, onEvent) => {
      const data = ChatStreamInputSchema.parse(input);
      return subscription(IPC_CHANNELS.chatStream, data.requestId, [data], onEvent);
    },
  },
  dialogs: {
    chooseSource: (kind) => {
      if (kind !== "pdf" && kind !== "markdown") return Promise.reject(new Error("INVALID_REQUEST"));
      return ipcRenderer.invoke(IPC_CHANNELS.dialogsChooseSource, kind).then((value: unknown) => value === null ? null : StagedSourceSchema.parse(value));
    },
  },
  shell: {
    openExternal: (url) => {
      let parsed: URL;
      try { parsed = new URL(url); } catch { return Promise.reject(new Error("INVALID_REQUEST")); }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return Promise.reject(new Error("INVALID_REQUEST"));
      return ipcRenderer.invoke(IPC_CHANNELS.shellOpenExternal, parsed.toString()).then(() => undefined);
    },
  },
};

for (const value of Object.values(api)) Object.freeze(value);
Object.freeze(api);
contextBridge.exposeInMainWorld("docmind", api);
