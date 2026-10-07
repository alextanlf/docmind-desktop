import type {
  Citation,
  DocMindApi,
  DocumentDetail,
  EventEnvelope,
  ImportJob,
  Message,
  ModelStatus,
  PluginManifest,
  RemoteCredentialChannel,
  RemoteProviderSummary,
  Repository,
  SessionSummary,
  SettingsView,
  RemoteStatus,
  SourcePreview,
  SourceFormat,
  StagedSource,
} from "../../shared/contracts";
import { vi } from "vitest";

export const readySettings: SettingsView = {
  model: {
    preset: "deepseek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-flash",
    timeoutSeconds: 30,
    reasoningEffort: "high",
  },
  hasApiKey: true,
  dataPath: "/Users/test/Library/Application Support/DocMind",
  screenshotCount: 2,
  webSearch: {
    mode: "ask",
    maxResults: 5,
    hasApiKey: false,
    queryRewrite: true,
    searxngUrl: "",
    modelSearchAvailable: false,
    modelSearchLabel: "",
    freeFallbackAvailable: true,
  },
  modelSetupSkipped: false,
  // Keyed by preset then model id, mirroring the backend's model-level table.
  // Values are the vendors' own effort levels (not a normalized scale), so they
  // must stay in sync with backend/app/core/model_capabilities.py.
  modelCapabilities: {
    deepseek: {
      "deepseek-flash": {
        reasoningLevels: ["off", "low", "high", "max"],
        defaultReasoningEffort: "high",
      },
    },
    kimi: {
      // K3 always reasons and tops out at max; K2.6 is a plain on/off toggle.
      "kimi-k3": { reasoningLevels: ["low", "high", "max"], defaultReasoningEffort: "max" },
      "kimi-k2.6": { reasoningLevels: ["off", "on"], defaultReasoningEffort: "on" },
    },
    glm: {
      // 4.6 only exposes the toggle; 5.3 cannot be disabled and has max.
      "glm-4.6": { reasoningLevels: ["off", "on"], defaultReasoningEffort: "on" },
      "glm-5.3": { reasoningLevels: ["low", "high", "max"], defaultReasoningEffort: "max" },
    },
  },
  modelPresets: {
    deepseek: [
      { id: "deepseek-flash", label: "DeepSeek Flash" },
      { id: "deepseek-v4-pro", label: "DeepSeek V4 Pro" },
    ],
    kimi: [
      { id: "kimi-k3", label: "Kimi K3" },
      { id: "kimi-k2.6", label: "Kimi K2.6" },
    ],
    glm: [
      { id: "glm-4.6", label: "GLM-4.6" },
      { id: "glm-5.3", label: "GLM-5.3" },
    ],
  },
};

export const unavailableEmbedding: ModelStatus = {
  state: "unavailable",
  // 与后端 config.py::embedding_model_name 默认值保持一致（打包态内置的也是 bge-m3）。
  // 早前这里写 bge-base-zh-v1.5/768，让测试固化了与生产不符的元数据。
  modelName: "BAAI/bge-m3",
  dimension: 1024,
  message: "模型尚未准备",
  progress: 0,
};

export const loggedOutRemote: RemoteStatus = {
  loggedIn: false,
  accountLabel: null,
  requiresLogin: true,
};

export const yuqueProviderSummaries: RemoteProviderSummary[] = [
  {
    name: "yuque",
    label: "语雀",
    configured: false,
    capabilities: {
      browserInstall: true,
      markerLookup: true,
      parentNodeWrite: false,
      browserUnavailableCode: "YUQUE_BROWSER_UNAVAILABLE",
    },
  },
];

export const feishuProviderSummary: RemoteProviderSummary = {
  name: "feishu",
  label: "飞书文档",
  configured: false,
  capabilities: {
    browserInstall: false,
    markerLookup: true,
    parentNodeWrite: true,
    browserUnavailableCode: null,
  },
};

export const allProviderSummaries: RemoteProviderSummary[] = [
  ...yuqueProviderSummaries,
  feishuProviderSummary,
];

export const yuqueCredentialChannels: RemoteCredentialChannel[] = [
  {
    provider: "yuque",
    channel: "web",
    label: "语雀网页登录",
    configured: false,
    state: "disconnected",
    accountLabel: null,
    hasSecret: false,
    purpose: "source",
    hint: "浏览器里直接登录，不用申请令牌；导入与同步时会打开 Chrome。",
  },
  {
    provider: "yuque",
    channel: "api",
    label: "语雀 API",
    configured: false,
    state: "disconnected",
    accountLabel: null,
    hasSecret: true,
    secretPlaceholder: "粘贴语雀个人访问令牌",
    helpUrl: "https://www.yuque.com/yuque/developer/api",
    helpLabel: "获取令牌",
    purpose: "source",
    hint: "更快更稳，但要先去语雀后台申请一个个人访问令牌。",
  },
];

export const feishuCredentialChannels: RemoteCredentialChannel[] = [
  {
    provider: "feishu",
    channel: "app",
    label: "飞书自建应用",
    configured: false,
    state: "disconnected",
    accountLabel: null,
    hasSecret: true,
    purpose: "source",
    hint: "填入 App ID 与 App Secret 后才能授权账号；这是飞书开放平台的要求。",
  },
  {
    provider: "feishu",
    channel: "user",
    label: "飞书账号授权",
    configured: false,
    state: "disconnected",
    accountLabel: null,
    hasSecret: false,
    purpose: "source",
    hint: "在浏览器里同意授权，让 DocMind 读取你的飞书文档。",
  },
  {
    provider: "feishu",
    channel: "webhook",
    label: "飞书机器人",
    configured: false,
    state: "disconnected",
    accountLabel: null,
    hasSecret: true,
    secretPlaceholder: "https://open.feishu.cn/open-apis/bot/v2/hook/…",
    helpUrl: "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
    helpLabel: "添加机器人",
    // A notification target, not a document source. The settings page groups
    // on this, so getting it wrong here would hide the grouping under test.
    purpose: "notify",
    hint: "导入完成或失败时，往群里发一条通知。不影响文档读取。",
  },
];

/**
 * The formats the backend serves for the import dialog.
 *
 * Mirrors `GET /api/imports/formats`, including the docx entry: the source
 * step must render a choice per entry rather than a fixed set, so a format
 * added on the backend appears without a renderer change.
 */
export const sourceFormats: SourceFormat[] = [
  {
    name: "pdf",
    label: "PDF",
    extensions: [".pdf"],
    mediaType: "application/pdf",
    maxBytes: 100 * 1024 * 1024,
  },
  {
    name: "markdown",
    label: "Markdown",
    extensions: [".md", ".markdown"],
    mediaType: "text/markdown",
    maxBytes: 20 * 1024 * 1024,
  },
  {
    name: "docx",
    label: "Word 文档",
    extensions: [".docx"],
    mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    maxBytes: 20 * 1024 * 1024,
  },
];

/**
 * The plugin catalogue, mirroring what the backend derives from the installed
 * contributions: one card per credential channel, plus one per contributed
 * document format.
 *
 * `acme` stands in for an installed third-party plugin — it has no counterpart
 * in DocMind's own source, which is the point: the renderer must present it
 * with no vendor-specific code. `tex:core` covers the other kind: a card whose
 * body is a static description rather than a credential form, and which
 * therefore reports no connection state.
 */
export const pluginManifests: PluginManifest[] = [
  {
    kind: "remote_source",
    id: "yuque:web",
    provider: "yuque",
    channel: "web",
    label: "语雀网页登录",
    providerLabel: "语雀",
    summary: "在浏览器里登录语雀，直接读取你的知识库",
    hint: "浏览器里直接登录，不用申请令牌；导入与同步时会打开 Chrome。",
    icon: "login",
    keywords: ["yuque", "语雀", "wiki", "浏览器", "扫码", "web"],
    tag: "知识库",
    extensions: [],
    homepage: "https://www.yuque.com/yuque/developer/api",
    version: null,
    hasSecret: false,
    secretPlaceholder: null,
    helpUrl: null,
    helpLabel: null,
    configured: false,
    state: "disconnected",
    accountLabel: null,
    browserInstall: true,
    browserUnavailableCode: "YUQUE_BROWSER_UNAVAILABLE",
  },
  {
    kind: "remote_source",
    id: "yuque:api",
    provider: "yuque",
    channel: "api",
    label: "语雀 API",
    providerLabel: "语雀",
    summary: "用个人访问令牌读取语雀知识库",
    hint: "更快更稳，但要先去语雀后台申请一个个人访问令牌。",
    icon: "key",
    keywords: ["yuque", "语雀", "wiki", "令牌", "token", "api", "个人访问令牌"],
    tag: "知识库",
    extensions: [],
    homepage: "https://www.yuque.com/yuque/developer/api",
    version: null,
    hasSecret: true,
    secretPlaceholder: "粘贴语雀个人访问令牌",
    helpUrl: "https://www.yuque.com/yuque/developer/api",
    helpLabel: "获取令牌",
    configured: false,
    state: "disconnected",
    accountLabel: null,
    browserInstall: true,
    browserUnavailableCode: "YUQUE_BROWSER_UNAVAILABLE",
  },
  {
    kind: "remote_source",
    id: "feishu:app",
    provider: "feishu",
    channel: "app",
    label: "飞书自建应用",
    providerLabel: "飞书文档",
    summary: "用自建应用的 App ID 与 App Secret 读取飞书文档",
    hint: "填入 App ID 与 App Secret 后才能授权账号；这是飞书开放平台的要求。",
    icon: "key",
    keywords: ["feishu", "飞书", "lark", "云文档", "应用", "app id", "app secret", "自建"],
    tag: "知识库",
    extensions: [],
    homepage: "https://open.feishu.cn/document/",
    version: null,
    hasSecret: true,
    secretPlaceholder: null,
    helpUrl: null,
    helpLabel: null,
    configured: false,
    state: "disconnected",
    accountLabel: null,
    browserInstall: false,
    browserUnavailableCode: null,
  },
  {
    kind: "remote_source",
    id: "feishu:user",
    provider: "feishu",
    channel: "user",
    label: "飞书账号授权",
    providerLabel: "飞书文档",
    summary: "用你的飞书账号授权，让 DocMind 读取你有权访问的文档",
    hint: "在浏览器里同意授权，让 DocMind 读取你的飞书文档。",
    icon: "login",
    keywords: ["feishu", "飞书", "lark", "云文档", "oauth", "授权", "账号", "user"],
    tag: "知识库",
    extensions: [],
    homepage: "https://open.feishu.cn/document/",
    version: null,
    hasSecret: false,
    secretPlaceholder: null,
    helpUrl: null,
    helpLabel: null,
    configured: false,
    state: "disconnected",
    accountLabel: null,
    browserInstall: false,
    browserUnavailableCode: null,
  },
  {
    kind: "remote_source",
    id: "feishu:webhook",
    provider: "feishu",
    channel: "webhook",
    label: "飞书机器人",
    providerLabel: "飞书文档",
    summary: "导入完成或失败时，往群里发一条通知",
    hint: "导入完成或失败时，往群里发一条通知。不影响文档读取。",
    icon: "bell",
    keywords: ["feishu", "飞书", "lark", "云文档", "机器人", "webhook", "群", "通知", "bot"],
    // Declared metadata, rendered as a tag. The page must NOT group on this —
    // a bot webhook used to sit in a「通知」section that read as a third way to
    // import documents.
    tag: "通知",
    extensions: [],
    homepage: "https://open.feishu.cn/document/",
    version: null,
    hasSecret: true,
    secretPlaceholder: "https://open.feishu.cn/open-apis/bot/v2/hook/…",
    helpUrl: "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
    helpLabel: "添加机器人",
    configured: false,
    state: "disconnected",
    accountLabel: null,
    browserInstall: false,
    browserUnavailableCode: null,
  },
  {
    kind: "remote_source",
    id: "acme:token",
    provider: "acme",
    channel: "token",
    label: "Acme 访问令牌",
    providerLabel: "Acme Wiki",
    summary: "用个人访问令牌读取 Acme Wiki",
    hint: "在 Acme 后台生成一个访问令牌。",
    icon: "key",
    keywords: ["acme", "wiki", "token", "令牌"],
    tag: "知识库",
    extensions: [],
    homepage: "https://acme.test",
    version: "0.1.0",
    hasSecret: true,
    secretPlaceholder: null,
    helpUrl: null,
    helpLabel: null,
    configured: false,
    state: "disconnected",
    accountLabel: null,
    browserInstall: false,
    browserUnavailableCode: null,
  },
  {
    kind: "document_format",
    id: "tex:core",
    provider: "tex",
    channel: "core",
    label: "TeX 文档",
    providerLabel: null,
    summary: "把 TeX 源文件导入 DocMind",
    hint: null,
    icon: "library",
    keywords: ["tex", "latex", "论文"],
    tag: "文档格式",
    extensions: [".tex", ".latex"],
    homepage: "https://example.test/tex-plugin",
    version: "0.1.0",
    hasSecret: false,
    secretPlaceholder: null,
    helpUrl: null,
    helpLabel: null,
    configured: false,
    state: "disconnected",
    accountLabel: null,
    browserInstall: false,
    browserUnavailableCode: null,
  },
];

export const repository: Repository = {
  id: "00000000-0000-0000-0000-000000000021",
  provider: "yuque",
  remoteId: "swiftui",
  name: "SwiftUI",
  description: "SwiftUI 知识库",
  remoteUrl: "https://www.yuque.com/test/swiftui",
  remoteParentId: null,
  documentCount: 1,
  indexedDocumentCount: 1,
  syncStatus: "已同步",
  createdAt: "2026-08-31T00:00:00Z",
  updatedAt: "2026-08-31T00:00:00Z",
};

export const document: DocumentDetail = {
  id: "00000000-0000-0000-0000-000000000022",
  repositoryId: repository.id,
  remoteId: "state-management",
  title: "State 管理",
  remoteUrl: "https://www.yuque.com/test/swiftui/state",
  chunkCount: 3,
  status: "已同步",
  remoteDeleted: false,
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
  localModel?: Partial<DocMindApi["localModel"]>;
  settings?: Partial<typeof window.docmind.settings>;
  embedding?: Partial<typeof window.docmind.embedding>;
  remote?: Partial<typeof window.docmind.remote>;
  plugins?: Partial<typeof window.docmind.plugins>;
  repositories?: Partial<DocMindApi["repositories"]>;
  sync?: Partial<DocMindApi["sync"]>;
  conflicts?: Partial<DocMindApi["conflicts"]>;
  versions?: Partial<DocMindApi["versions"]>;
  graph?: Partial<DocMindApi["graph"]>;
  documents?: Partial<DocMindApi["documents"]>;
  imports?: Partial<DocMindApi["imports"]>;
  chat?: Partial<DocMindApi["chat"]>;
  dialogs?: Partial<DocMindApi["dialogs"]>;
  shell?: Partial<DocMindApi["shell"]>;
  app?: Partial<DocMindApi["app"]>;
  sources?: Partial<DocMindApi["sources"]>;
  batches?: Partial<DocMindApi["batches"]>;
  memory?: Partial<DocMindApi["memory"]>;
  webSearch?: Partial<DocMindApi["webSearch"]>;
}) {
  const api = {
    localModel: {
      status: vi.fn().mockResolvedValue({
        available: false,
        baseUrl: "http://127.0.0.1:11434",
        version: null,
        selectedModel: "",
        selectedModelAvailable: false,
        checkedAt: "2026-08-31T08:00:00Z",
        message: "本地模型服务未运行或暂时无法连接",
      }),
      models: vi.fn().mockResolvedValue({
        available: false,
        models: [],
        checkedAt: "2026-08-31T08:00:00Z",
        message: "本地模型服务未运行或暂时无法连接",
      }),
      ...overrides?.localModel,
    },
    settings: {
      get: vi.fn().mockResolvedValue(readySettings),
      saveModel: vi.fn().mockResolvedValue(readySettings),
      testModel: vi.fn().mockResolvedValue({ connected: true, latencyMs: 86 }),
      listModels: vi.fn().mockResolvedValue({ models: [], source: "live", notice: null }),
      skipModelSetup: vi.fn().mockResolvedValue(readySettings),
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
        // 措辞跟随后端：是「加载」不是「下载」——模型随应用分发，不走网络。
        message: "正在准备嵌入模型",
      }),
      ...overrides?.embedding,
    },
    remote: {
      status: vi.fn().mockResolvedValue(loggedOutRemote),
      login: vi.fn().mockResolvedValue({
        loggedIn: true,
        accountLabel: "DocMind 测试账号",
        requiresLogin: false,
      }),
      installBrowser: vi.fn().mockResolvedValue({
        installed: true,
        message: "语雀浏览器已安装",
      }),
      listProviders: vi.fn().mockResolvedValue(allProviderSummaries),
      listCredentials: vi
        .fn()
        .mockImplementation((provider: string) =>
          Promise.resolve(
            provider === "feishu" ? feishuCredentialChannels : yuqueCredentialChannels,
          ),
        ),
      saveCredential: vi.fn().mockResolvedValue({
        ...yuqueCredentialChannels[1],
        configured: true,
        state: "unverified",
      }),
      testCredential: vi.fn().mockResolvedValue({
        connected: true,
        message: "语雀 API 已连接",
        label: "t***t",
      }),
      deleteCredential: vi.fn().mockResolvedValue({
        ...yuqueCredentialChannels[1],
        configured: false,
        state: "disconnected",
      }),
      ...overrides?.remote,
    },
    plugins: {
      // One call for the whole catalogue. The renderer filters locally, so the
      // query argument stays unused here — it exists for a catalogue too large
      // to ship wholesale.
      list: vi.fn().mockResolvedValue(pluginManifests),
      diagnostics: vi.fn().mockResolvedValue([]),
      ...overrides?.plugins,
    },
    repositories: {
      list: vi.fn().mockResolvedValue([repository]),
      create: vi.fn().mockResolvedValue(repository),
      update: vi.fn().mockResolvedValue(repository),
      ...overrides?.repositories,
    },
    sync: {
      get: vi.fn().mockResolvedValue({ lastSyncedAt: null }),
      trigger: vi.fn().mockResolvedValue({
        repositoryId: "",
        added: 0,
        changed: 0,
        deleted: 0,
        unchanged: 0,
        failed: 0,
        startedAt: "",
        finishedAt: "",
      }),
      ...overrides?.sync,
    },
    conflicts: {
      list: vi.fn().mockResolvedValue([]),
      resolve: vi.fn().mockResolvedValue(undefined),
      ...overrides?.conflicts,
    },
    versions: {
      list: vi.fn().mockResolvedValue([]),
      ...overrides?.versions,
    },
    graph: {
      nodes: vi.fn().mockResolvedValue([]),
      adjacency: vi.fn().mockResolvedValue([]),
      ...overrides?.graph,
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
    app: {
      restart: vi.fn().mockResolvedValue(undefined),
      ...overrides?.app,
    },
    sources: {
      stageDirectory: vi.fn().mockResolvedValue(null),
      listFormats: vi.fn().mockResolvedValue(sourceFormats),
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
