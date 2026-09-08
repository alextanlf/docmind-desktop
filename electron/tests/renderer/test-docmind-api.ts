import type {
  Citation,
  DocMindApi,
  DocumentDetail,
  EventEnvelope,
  ImportJob,
  Message,
  ModelStatus,
  Repository,
  SessionSummary,
  SettingsView,
  SourcePreview,
  StagedSource,
  YuqueStatus,
} from "../../shared/contracts";
import { vi } from "vitest";

export const readySettings: SettingsView = {
  model: {
    preset: "deepseek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-chat",
    timeoutSeconds: 30,
  },
  hasApiKey: true,
  dataPath: "/Users/test/Library/Application Support/DocMind",
  screenshotCount: 2,
  webSearch: { provider: "tavily", mode: "ask", maxResults: 5, hasApiKey: false },
};

export const unavailableEmbedding: ModelStatus = {
  state: "unavailable",
  modelName: "BAAI/bge-base-zh-v1.5",
  dimension: null,
  message: "尚未下载",
  progress: 0,
};

export const loggedOutYuque: YuqueStatus = {
  loggedIn: false,
  accountLabel: null,
  requiresLogin: true,
};

export const repository: Repository = {
  id: "00000000-0000-0000-0000-000000000021",
  yuqueId: "swiftui",
  name: "SwiftUI",
  description: "SwiftUI 知识库",
  yuqueUrl: "https://www.yuque.com/test/swiftui",
  documentCount: 1,
  indexedDocumentCount: 1,
  syncStatus: "已同步",
  createdAt: "2026-08-31T00:00:00Z",
  updatedAt: "2026-08-31T00:00:00Z",
};

export const document: DocumentDetail = {
  id: "00000000-0000-0000-0000-000000000022",
  repositoryId: repository.id,
  yuqueId: "state-management",
  title: "State 管理",
  yuqueUrl: "https://www.yuque.com/test/swiftui/state",
  chunkCount: 3,
  status: "已同步",
  content: "# State 管理\n\n内容",
  createdAt: "2026-08-31T00:00:00Z",
  updatedAt: "2026-08-31T00:00:00Z",
};

export const source: StagedSource = {
  stagedSourceId: "00000000-0000-0000-0000-000000000023",
  kind: "staged_file",
  name: "guide.md",
  mediaType: "text/markdown",
  sizeBytes: 1024,
};

export const preview: SourcePreview = {
  title: "guide.md",
  sourceKind: "staged_file",
  sourceUrl: null,
  mediaType: "text/markdown",
  sizeBytes: 1024,
  fingerprint: "a".repeat(64),
  warnings: [],
};

export const job: ImportJob = {
  id: "00000000-0000-0000-0000-000000000024",
  source: { kind: "staged_file", value: source.stagedSourceId },
  repositoryId: repository.id,
  state: "pending",
  currentStage: "准备导入",
  progress: 0,
  message: "等待导入",
  errorCode: null,
  errorMessage: null,
  retryable: false,
  documentId: null,
  cancelRequested: false,
  createdAt: "2026-08-31T00:00:00Z",
  startedAt: null,
  completedAt: null,
  updatedAt: "2026-08-31T00:00:00Z",
};

export const session: SessionSummary = {
  id: "00000000-0000-0000-0000-000000000025",
  title: "State 管理问题",
  repositoryIds: [repository.id],
  createdAt: "2026-08-31T08:00:00Z",
  updatedAt: "2026-08-31T08:00:00Z",
};

export const citation: Citation = {
  kind: "document",
  sourceId: "S1",
  chunkId: "chunk-state-1",
  documentId: document.id,
  title: "状态管理",
  sectionPath: "状态管理 > @State",
  pageNumber: null,
  excerpt: "@State 用于管理视图内部的可变状态。",
  sourceUrl: "https://www.yuque.com/test/swiftui/state",
};

export function installDocMindApi(overrides?: {
  ollama?: Partial<DocMindApi["ollama"]>;
  settings?: Partial<typeof window.docmind.settings>;
  embedding?: Partial<typeof window.docmind.embedding>;
  yuque?: Partial<typeof window.docmind.yuque>;
  repositories?: Partial<DocMindApi["repositories"]>;
  documents?: Partial<DocMindApi["documents"]>;
  imports?: Partial<DocMindApi["imports"]>;
  chat?: Partial<DocMindApi["chat"]>;
  dialogs?: Partial<DocMindApi["dialogs"]>;
  shell?: Partial<DocMindApi["shell"]>;
  sources?: Partial<DocMindApi["sources"]>;
  batches?: Partial<DocMindApi["batches"]>;
  memory?: Partial<DocMindApi["memory"]>;
  webSearch?: Partial<DocMindApi["webSearch"]>;
}) {
  const api = {
    ollama: {
      status: vi.fn().mockResolvedValue({
        available: false,
        baseUrl: "http://127.0.0.1:11434",
        version: null,
        selectedModel: "llama3.2",
        selectedModelInstalled: false,
        checkedAt: "2026-08-31T08:00:00Z",
        message: "Ollama 未运行",
      }),
      models: vi.fn().mockResolvedValue({
        available: false,
        models: [],
        checkedAt: "2026-08-31T08:00:00Z",
        message: "Ollama 未运行",
      }),
      pull: vi.fn(),
      getPull: vi.fn(),
      cancelPull: vi.fn(),
      retryPull: vi.fn(),
      subscribePull: vi
        .fn()
        .mockReturnValue({ requestId: "pull", cancel: vi.fn(), detach: vi.fn() }),
      ...overrides?.ollama,
    },
    settings: {
      get: vi.fn().mockResolvedValue(readySettings),
      saveModel: vi.fn().mockResolvedValue(readySettings),
      testModel: vi.fn().mockResolvedValue({ connected: true, latencyMs: 86 }),
      clearDiagnostics: vi.fn().mockResolvedValue(undefined),
      saveWebSearch: vi.fn().mockResolvedValue(readySettings),
      ...overrides?.settings,
    },
    embedding: {
      status: vi.fn().mockResolvedValue(unavailableEmbedding),
      prepare: vi.fn().mockResolvedValue({
        ...unavailableEmbedding,
        state: "downloading",
        progress: 12,
        message: "正在下载",
      }),
      ...overrides?.embedding,
    },
    yuque: {
      status: vi.fn().mockResolvedValue(loggedOutYuque),
      login: vi.fn().mockResolvedValue({
        loggedIn: true,
        accountLabel: "DocMind 测试账号",
        requiresLogin: false,
      }),
      ...overrides?.yuque,
    },
    repositories: {
      list: vi.fn().mockResolvedValue([repository]),
      create: vi.fn().mockResolvedValue(repository),
      ...overrides?.repositories,
    },
    documents: {
      list: vi.fn().mockResolvedValue([document]),
      read: vi.fn().mockResolvedValue(document),
      create: vi.fn().mockResolvedValue(document),
      update: vi.fn().mockResolvedValue(document),
      delete: vi.fn().mockResolvedValue(undefined),
      ...overrides?.documents,
    },
    imports: {
      inspect: vi.fn().mockResolvedValue(preview),
      create: vi.fn().mockResolvedValue(job),
      get: vi.fn().mockResolvedValue(job),
      retry: vi.fn().mockResolvedValue(job),
      cancel: vi.fn().mockResolvedValue({ ...job, state: "cancelled" }),
      subscribe: vi.fn().mockReturnValue({ requestId: job.id, cancel: vi.fn(), detach: vi.fn() }),
      ...overrides?.imports,
    },
    chat: {
      listSessions: vi.fn().mockResolvedValue([session]),
      createSession: vi.fn().mockResolvedValue(session),
      listMessages: vi.fn().mockResolvedValue([]),
      stream: vi.fn(),
      searchStream: vi.fn(),
      ...overrides?.chat,
    },
    webSearch: {
      getRun: vi.fn(),
      createImportBatch: vi.fn(),
      ...overrides?.webSearch,
    },
    memory: {
      endSession: vi.fn().mockResolvedValue({ ...session, endedAt: "2026-08-31T09:00:00Z" }),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      getSummary: vi.fn().mockResolvedValue(null),
      regenerateSummary: vi.fn(),
      deleteSummary: vi.fn().mockResolvedValue(undefined),
      createDistillation: vi.fn(),
      getDistillation: vi.fn(),
      updateDistillation: vi.fn(),
      regenerateDistillation: vi.fn(),
      saveDistillation: vi.fn(),
      deleteDistillation: vi.fn().mockResolvedValue(undefined),
      list: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
      subscribeDistillation: vi.fn().mockReturnValue({
        requestId: "00000000-0000-0000-0000-000000000026",
        cancel: vi.fn(),
        detach: vi.fn(),
      }),
      ...overrides?.memory,
    },
    dialogs: {
      chooseSource: vi.fn().mockResolvedValue(source),
      ...overrides?.dialogs,
    },
    shell: {
      openExternal: vi.fn().mockResolvedValue(undefined),
      ...overrides?.shell,
    },
    sources: {
      stageDirectory: vi.fn().mockResolvedValue(null),
      ...overrides?.sources,
    },
    batches: {
      create: vi.fn(),
      get: vi.fn(),
      list: vi.fn().mockResolvedValue([]),
      listItems: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
      confirm: vi.fn(),
      cancel: vi.fn(),
      continue: vi.fn(),
      retry: vi.fn(),
      subscribe: vi.fn().mockReturnValue({ requestId: "batch", cancel: vi.fn(), detach: vi.fn() }),
      ...overrides?.batches,
    },
  };

  Object.defineProperty(window, "docmind", {
    configurable: true,
    value: api as DocMindApi,
  });
  return api;
}

export function installChatStreamMock(chat: DocMindApi["chat"]) {
  let onEvent: ((event: EventEnvelope) => void) | null = null;
  let input: Parameters<DocMindApi["chat"]["stream"]>[0] | null = null;
  let accumulatedContent = "";
  let accumulatedCitations: Citation[] = [];
  let lastSequence = 0;
  const cancel = vi.fn();
  const detach = vi.fn();
  const messages: Message[] = [];

  vi.mocked(chat.listMessages).mockImplementation(async () => messages);
  vi.mocked(chat.stream).mockImplementation((nextInput, listener) => {
    input = nextInput;
    onEvent = listener;
    return { requestId: nextInput.requestId, cancel, detach };
  });

  return {
    get requestId() {
      return input?.requestId ?? "";
    },
    cancel,
    detach,
    emit(event: EventEnvelope) {
      const ordered = event.sequence > lastSequence;
      if (ordered) lastSequence = event.sequence;
      if (ordered && event.type === "delta" && typeof event.payload.content === "string") {
        accumulatedContent += event.payload.content;
      }
      if (ordered && event.type === "citations" && Array.isArray(event.payload.citations)) {
        accumulatedCitations = event.payload.citations as Citation[];
      }
      if (ordered && event.type === "done" && input) {
        const messageId =
          typeof event.payload.messageId === "string" ? event.payload.messageId : "";
        messages.splice(
          0,
          messages.length,
          {
            id: "00000000-0000-0000-0000-000000000026",
            sessionId: input.sessionId,
            role: "user",
            content: input.message,
            citations: [],
            generationStatus: "completed",
            createdAt: "2026-08-31T08:01:00Z",
          },
          {
            id: messageId,
            sessionId: input.sessionId,
            role: "assistant",
            content: accumulatedContent,
            citations: accumulatedCitations,
            generationStatus: "completed",
            createdAt: "2026-08-31T08:01:01Z",
          },
        );
      }
      onEvent?.(event);
    },
  };
}
