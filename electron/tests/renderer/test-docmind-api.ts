import type {
  DocMindApi,
  DocumentDetail,
  ImportJob,
  ModelStatus,
  Repository,
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

export function installDocMindApi(overrides?: {
  settings?: Partial<typeof window.docmind.settings>;
  embedding?: Partial<typeof window.docmind.embedding>;
  yuque?: Partial<typeof window.docmind.yuque>;
  repositories?: Partial<DocMindApi["repositories"]>;
  documents?: Partial<DocMindApi["documents"]>;
  imports?: Partial<DocMindApi["imports"]>;
  dialogs?: Partial<DocMindApi["dialogs"]>;
  shell?: Partial<DocMindApi["shell"]>;
}) {
  const api = {
    settings: {
      get: vi.fn().mockResolvedValue(readySettings),
      saveModel: vi.fn().mockResolvedValue(readySettings),
      testModel: vi.fn().mockResolvedValue({ connected: true, latencyMs: 86 }),
      clearDiagnostics: vi.fn().mockResolvedValue(undefined),
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
      subscribe: vi.fn().mockReturnValue({ requestId: job.id, cancel: vi.fn() }),
      ...overrides?.imports,
    },
    dialogs: {
      chooseSource: vi.fn().mockResolvedValue(source),
      ...overrides?.dialogs,
    },
    shell: {
      openExternal: vi.fn().mockResolvedValue(undefined),
      ...overrides?.shell,
    },
  };

  Object.defineProperty(window, "docmind", {
    configurable: true,
    value: api as DocMindApi,
  });
  return api;
}
