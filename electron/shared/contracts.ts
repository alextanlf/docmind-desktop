import { z } from "zod";

const id = z.string().uuid();
const text = (max: number) => z.string().trim().min(1).max(max);
const bounded = (max: number) => z.string().min(1).max(max);
const nullableText = (max: number) => z.string().max(max).nullable();
const timestamp = z.string().min(1);

export const RagSettingsSchema = z.object({
  maxSources: z.number().int().min(1).max(20),
  memoryRecallMinSimilarity: z.number().min(-1).max(1),
});
const LocalModelRuntimeSchema = z.object({
  baseUrl: z.string(),
  model: z.string().max(200),
  apiKey: z.string().max(500).default(""),
  timeoutSeconds: z.number().positive().max(600),
});
const RoutingSchema = z.object({ mode: z.enum(["local_only", "cloud_only", "automatic"]) });
const RagRuntimeSchema = RagSettingsSchema.default({
  maxSources: 5,
  memoryRecallMinSimilarity: 0.65,
});

export const ErrorBodySchema = z.object({
  code: z.string().min(1).max(128),
  message: z.string().min(1).max(2_000),
  retryable: z.boolean().default(false),
  action: z.string().max(500).nullable().optional(),
});
export const ErrorEnvelopeSchema = z.object({ error: ErrorBodySchema });
export const IpcResultSchema = <T extends z.ZodTypeAny>(value: T) =>
  z.discriminatedUnion("ok", [
    z.object({ ok: z.literal(true), value }),
    z.object({ ok: z.literal(false), error: ErrorBodySchema }),
  ]);

/**
 * Model preset ids — single source of truth shared by the input schema and the
 * renderer form.  Keep the backend `MODEL_PRESETS` keys in sync (there is no
 * cross-language link; `contracts.test.ts` asserts both sides of this pair).
 */
export const MODEL_PRESET_IDS = [
  "deepseek",
  "qwen",
  "kimi",
  "glm",
  "mimo",
  "opencode_zen",
  "opencode_go",
  "openai",
  "custom",
] as const;

export const ModelPresetSchema = z.enum(MODEL_PRESET_IDS);
export type ModelPreset = z.infer<typeof ModelPresetSchema>;

export const ModelSettingsInputSchema = z.object({
  preset: ModelPresetSchema,
  reasoningEffort: z.string().max(32).default(""),
  baseUrl: z.string().max(500),
  model: z.string().max(200),
  timeoutSeconds: z.number().positive().max(300),
  apiKey: z.string().max(2_000).nullable().optional(),
});
export const ModelSettingsViewSchema = z.object({
  reasoningEffort: z.string().max(32).default(""),
  preset: z.string(),
  baseUrl: z.string(),
  model: z.string(),
  timeoutSeconds: z.number(),
});
export const ConnectionTestResultSchema = z.object({
  connected: z.boolean(),
  message: z.string(),
  label: z.string().nullable().optional(),
});
export const AvailableModelSchema = z.object({
  id: z.string().min(1).max(200),
  label: z.string().min(1).max(200),
});

export const SettingsViewSchema = z.object({
  model: ModelSettingsViewSchema,
  hasApiKey: z.boolean(),
  dataPath: z.string(),
  screenshotCount: z.number().int().nonnegative(),
  webSearch: z
    .object({
      mode: z.enum(["off", "ask", "auto"]),
      maxResults: z.number().int().min(1).max(10),
      hasApiKey: z.boolean(),
      queryRewrite: z.boolean().default(true),
      searxngUrl: z.string().max(500).default(""),
      modelSearchAvailable: z.boolean().default(false),
      modelSearchLabel: z.string().max(200).default(""),
      freeFallbackAvailable: z.boolean().default(true),
    })
    .default({
      mode: "ask",
      maxResults: 5,
      hasApiKey: false,
      queryRewrite: true,
      searxngUrl: "",
      modelSearchAvailable: false,
      modelSearchLabel: "",
      freeFallbackAvailable: true,
    }),
  runtime: z
    .object({
      local: LocalModelRuntimeSchema,
      routing: RoutingSchema,
      rag: RagRuntimeSchema,
    })
    .optional(),
  modelPresets: z.record(z.string(), z.array(AvailableModelSchema)).default({}),
  modelSetupSkipped: z.boolean().default(false),
  // Keyed by preset, then by model id: the same vendor's models differ.
  modelCapabilities: z
    .record(
      z.string(),
      z.record(
        z.string(),
        z.object({
          reasoningLevels: z.array(z.string()).default([]),
          defaultReasoningEffort: z.string().default(""),
        }),
      ),
    )
    .default({}),
});
export const RuntimeSettingsInputSchema = z.object({
  local: LocalModelRuntimeSchema,
  routing: RoutingSchema,
  rag: RagRuntimeSchema,
});
export const LocalModelStatusSchema = z.object({
  available: z.boolean(),
  baseUrl: z.string(),
  version: z.string().nullable().optional(),
  selectedModel: z.string(),
  selectedModelAvailable: z.boolean(),
  checkedAt: timestamp,
  message: z.string(),
});
export const LocalModelSchema = z.object({
  id: z.string(),
  label: z.string(),
});
export const LocalModelsSchema = z.object({
  available: z.boolean(),
  models: LocalModelSchema.array(),
  checkedAt: timestamp,
  message: z.string(),
});
export type LocalModelStatusView = z.infer<typeof LocalModelStatusSchema>;
export type LocalModelView = z.infer<typeof LocalModelSchema>;
export type LocalModelsView = z.infer<typeof LocalModelsSchema>;
export const WebSearchSettingsInputSchema = z.object({
  mode: z.enum(["off", "ask", "auto"]),
  maxResults: z.number().int().min(1).max(10),
  queryRewrite: z.boolean(),
  searxngUrl: z.string().max(500),
  apiKey: z.string().max(2_000).nullable().optional(),
});
export const ModelConnectionResultSchema = z.object({
  connected: z.boolean(),
  latencyMs: z.number().int().nonnegative(),
});

export const ModelListProbeSchema = z.object({
  // The preset must be sent: the backend falls back to the *saved* preset's
  // catalogue without it, so a user picking a vendor they never saved would
  // get an empty list.
  preset: z.string().max(32).optional(),
  baseUrl: z.string().max(500).optional(),
  model: z.string().max(200).optional(),
  apiKey: z.string().max(2_000).optional(),
});

export const ModelListViewSchema = z.object({
  models: z.array(AvailableModelSchema).default([]),
  // "curated" means the provider did not answer and the built-in list was used;
  // the form must not claim those came from the provider.
  source: z.enum(["live", "curated"]).default("live"),
  notice: z.string().max(500).nullable().optional(),
});

export const ModelStatusSchema = z.object({
  state: z.enum(["unavailable", "downloading", "ready", "error"]),
  modelName: z.string(),
  dimension: z.number().int().positive().nullable().optional(),
  message: z.string(),
  progress: z.number().int().min(0).max(100).nullable().optional(),
});
export const RemoteStatusSchema = z.object({
  loggedIn: z.boolean(),
  accountLabel: z.string().nullable().optional(),
  requiresLogin: z.boolean(),
});
export const BrowserInstallResultSchema = z.object({
  installed: z.boolean(),
  message: z.string(),
});
export const RemoteProviderCapabilitiesSchema = z.object({
  browserInstall: z.boolean(),
  markerLookup: z.boolean(),
  parentNodeWrite: z.boolean(),
  /**
   * Error code this provider raises when its login browser is missing.
   * Declared by the provider so the install action never hardcodes a vendor.
   */
  browserUnavailableCode: z.string().nullable().optional(),
});
export const RemoteProviderSummarySchema = z.object({
  name: z.string(),
  label: z.string(),
  configured: z.boolean(),
  capabilities: RemoteProviderCapabilitiesSchema,
});
export const RemoteCredentialChannelSchema = z.object({
  provider: z.string(),
  channel: z.string(),
  label: z.string(),
  configured: z.boolean(),
  state: z.enum(["verified", "unverified", "disconnected"]),
  accountLabel: z.string().nullable().optional(),
  hasSecret: z.boolean(),
  /** Channel-declared input hint; a URL-shaped secret overrides the default copy. */
  secretPlaceholder: z.string().nullable().optional(),
  helpUrl: z.string().nullable().optional(),
  helpLabel: z.string().nullable().optional(),
  /**
   * What the channel is for. `source` provides documents; `notify` only
   * delivers notifications. The settings page groups on this so a bot webhook
   * is not listed among the knowledge-base sources.
   */
  purpose: z.enum(["source", "notify"]).default("source"),
  /** Channel-declared trade-off, e.g. "opens a browser" / "needs a token". */
  hint: z.string().nullable().optional(),
});
export const RemoteCredentialTestResultSchema = z.object({
  connected: z.boolean(),
  message: z.string(),
  label: z.string().nullable().optional(),
});
export const SaveRemoteCredentialInputSchema = z.object({
  secret: z.string().max(4_000),
});

/**
 * One plugin card. What a plugin contributes varies — a credential channel for
 * a remote knowledge base, a document format, something not invented yet — so
 * `kind` is the discriminator the renderer uses to pick a card *body*. It
 * selects a body, never a vendor: an unknown kind falls back to a neutral card.
 *
 * Everything rendered here is plugin-declared (label, providerLabel, summary,
 * icon, keywords, tag). The renderer holds no list of its own and names no
 * vendor — installing a third-party plugin needs no change on this side.
 */
export const PluginManifestSchema = z.object({
  id: z.string(),
  /** Which kind of capability this card configures; picks the card body. */
  kind: z.string(),
  provider: z.string(),
  channel: z.string(),
  label: z.string(),
  /**
   * Which integration this plugin plugs into; shown as the card subtitle.
   * `null` for a contribution that plugs into nothing (a format), where a
   * subtitle would be noise rather than orientation.
   */
  providerLabel: z.string().nullable().optional(),
  summary: z.string().nullable().optional(),
  hint: z.string().nullable().optional(),
  /** Glyph key resolved by the renderer's icon table; unknown keys fall back. */
  icon: z.string().nullable().optional(),
  /** Alias terms a user might type that the labels don't contain. */
  keywords: z.array(z.string()).default([]),
  /**
   * The category tag's display text ("知识库" / "通知" / "文档格式"), declared by
   * the plugin. Rendered verbatim — the page must not know what a category is
   * called, and must not use it to group: it is a tag, not a section heading.
   */
  tag: z.string().nullable().optional(),
  homepage: z.string().nullable().optional(),
  version: z.string().nullable().optional(),
  /** Suffixes this card adds ("支持 .tex"); empty for anything but a format. */
  extensions: z.array(z.string()).default([]),
  hasSecret: z.boolean(),
  secretPlaceholder: z.string().nullable().optional(),
  helpUrl: z.string().nullable().optional(),
  helpLabel: z.string().nullable().optional(),
  configured: z.boolean(),
  state: z.enum(["verified", "unverified", "disconnected"]),
  accountLabel: z.string().nullable().optional(),
  browserInstall: z.boolean(),
  browserUnavailableCode: z.string().nullable().optional(),
});

/**
 * One line of the plugin diagnostics report: a plugin that was found, and what
 * happened to it.
 *
 * `error` is present and empty for a plugin that loaded, so the shape does not
 * depend on the outcome — the common case in a working install is a plugin that
 * loaded, and a client that had to special-case a missing key would eventually
 * treat that case as malformed. `source` is the directory the plugin was found
 * in, empty for an installed distribution; it is what a developer needs in
 * order to know which clone to fix.
 */
export const PluginDiagnosticSchema = z.object({
  name: z.string(),
  error: z.string(),
  source: z.string(),
});

/**
 * Where plugins are installed by putting them. Reported rather than documented:
 * the path is derived from the application's data directory, which differs per
 * platform and follows the application's own name, so a path written into a
 * guide goes stale and leaves the user cloning into a directory nothing reads.
 */
export const PluginDirectorySchema = z.object({ path: z.string() });

export const RepositorySchema = z.object({
  id,
  provider: z.string().max(32).nullable().optional(),
  remoteId: z.string().nullable().optional(),
  name: text(120),
  description: nullableText(2_000),
  remoteUrl: z.string().max(4_000).nullable().optional(),
  remoteParentId: z.string().max(255).nullable().optional(),
  documentCount: z.number().int().nonnegative(),
  indexedDocumentCount: z.number().int().nonnegative(),
  syncStatus: z.string(),
  createdAt: timestamp,
  updatedAt: timestamp,
});
export const CreateRepositoryInputSchema = z.object({
  name: text(120),
  // Omitted or null creates a local knowledge base (the default).
  provider: z.string().max(32).nullable().optional(),
});
export const UpdateRepositoryInputSchema = z.object({
  remoteParentId: z.string().max(255).nullable(),
});

export const DocumentInputSchema = z.object({
  title: text(240),
  content: bounded(2_000_000),
});
export const DocumentSummarySchema = z.object({
  id,
  repositoryId: id,
  remoteId: z.string().nullable().optional(),
  title: text(240),
  remoteUrl: z.string().max(4_000).nullable().optional(),
  chunkCount: z.number().int().nonnegative(),
  status: z.string(),
  remoteDeleted: z.boolean(),
  createdAt: timestamp,
  updatedAt: timestamp,
});

export const SyncOutcomeSchema = z.object({
  repositoryId: z.string(),
  added: z.number().int().nonnegative(),
  changed: z.number().int().nonnegative(),
  deleted: z.number().int().nonnegative(),
  unchanged: z.number().int().nonnegative(),
  failed: z.number().int().nonnegative(),
  startedAt: z.string(),
  finishedAt: z.string(),
});

export const SyncStatusSchema = z.object({ lastSyncedAt: z.string().nullable() });

export const ConflictViewSchema = z.object({
  documentId: z.string(),
  title: text(240),
  localContent: bounded(2_000_000),
  remoteContent: bounded(2_000_000),
});

export const ConflictResolutionSchema = z.enum(["keep_local", "keep_remote", "keep_both"]);

export const DocumentVersionSchema = z.object({
  documentId: z.string(),
  versionNo: z.number().int().nonnegative(),
  title: text(240),
  content: bounded(2_000_000),
  contentSha256: z.string(),
  createdAt: z.string(),
});

export const GraphNodeSchema = z.object({
  id: z.string(),
  kind: z.enum(["document", "topic", "citation", "tag"]),
  label: text(240),
  documentId: z.string().nullable().optional(),
});

export const GraphEdgeSchema = z.object({
  sourceId: z.string(),
  targetId: z.string(),
  relation: z.enum(["references", "mentions", "tags"]),
});
export const DocumentDetailSchema = DocumentSummarySchema.extend({
  content: z.string().max(2_000_000),
  /**
   * 原件预览能力。为 null 表示磁盘上没有可识别的原件（远程文档、原件被删），
   * 渲染层据此隐藏「原文」视图 —— 不要靠试错去发现。
   */
  originalMediaType: z.string().max(255).nullable(),
  originalByteSize: z.number().int().nonnegative().nullable(),
});

/**
 * 原件分片。二进制以 base64 过 IPC：`contextBridge` 不保证透传 TypedArray，
 * 而本项目已有同款约定（见后端 `_document_snapshot` 的 `file_bytes_b64`）。
 */
export const DocumentOriginalChunkSchema = z.object({
  data: z.string(),
  total: z.number().int().nonnegative(),
});

export const SourceRefSchema = z.object({
  kind: z.enum(["url", "staged_file"]),
  value: bounded(4_000),
});
export const SourcePreviewSchema = z.preprocess(
  (value) => {
    if (!value || typeof value !== "object") return value;
    const raw = value as Record<string, unknown>;
    if (raw.sourceKind === "staged_file") return { ...raw, sourceUrl: null };
    return raw;
  },
  z.object({
    title: text(240),
    sourceKind: z.enum(["url", "staged_file"]),
    sourceUrl: z.string().max(4_000).nullable().optional(),
    mediaType: z.string(),
    sizeBytes: z.number().int().nonnegative(),
    fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
    warnings: z.array(z.string().max(500)),
  }),
);
export const CreateImportInputSchema = z.object({
  source: SourceRefSchema,
  repositoryId: id,
  fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  duplicateDecision: z.enum(["skip", "update"]).nullable().optional(),
});
export const ImportJobSchema = z.object({
  id,
  source: SourceRefSchema,
  repositoryId: id.nullable().optional(),
  state: z.enum([
    "pending",
    "parsing",
    "uploading",
    "indexing",
    "completed",
    "failed",
    "cancelled",
  ]),
  currentStage: z.string().nullable().optional(),
  progress: z.number().int().min(0).max(100),
  message: z.string(),
  errorCode: z.string().nullable().optional(),
  errorMessage: z.string().nullable().optional(),
  retryable: z.boolean(),
  documentId: id.nullable().optional(),
  cancelRequested: z.boolean(),
  createdAt: timestamp,
  startedAt: timestamp.nullable().optional(),
  completedAt: timestamp.nullable().optional(),
  updatedAt: timestamp,
});

export const CreateSessionInputSchema = z.object({
  repositoryIds: z.array(id).max(100),
});
export const SessionSummarySchema = z.object({
  id,
  title: text(512),
  repositoryIds: z.array(id),
  createdAt: timestamp,
  updatedAt: timestamp,
  endedAt: timestamp.nullable().optional(),
});
const DocumentCitationSchema = z.object({
  kind: z.literal("document"),
  sourceId: z.string().regex(/^S[1-9]\d*$/),
  chunkId: z.string().max(255),
  documentId: id,
  title: text(240),
  sectionPath: z.string().max(1_000).nullable().optional(),
  pageNumber: z.number().int().positive().nullable().optional(),
  excerpt: z.string().max(5_000),
  sourceUrl: z.string().max(4_000).nullable().optional(),
});
const MemoryCitationSchema = z.object({
  kind: z.literal("memory"),
  sourceId: z.string().regex(/^M[1-9]\d*$/),
  memoryKind: z.enum(["session_summary", "distillation"]),
  memoryId: id,
  title: text(240),
  excerpt: z.string().max(5_000),
  sessionId: id.nullable().optional(),
});
const WebCitationSchema = z.object({
  kind: z.literal("web"),
  sourceId: z.string().regex(/^W[1-9]\d*$/),
  searchRunId: id,
  resultId: id,
  title: text(240),
  excerpt: z.string().max(5_000),
  sourceUrl: z.string().url().max(4_000),
  retrievedAt: timestamp,
});
export const CitationSchema = z.preprocess(
  (value) => {
    if (!value || typeof value !== "object") return value;
    const raw = value as Record<string, unknown>;
    const sourceUrl = raw.sourceUrl;
    if (
      typeof sourceUrl === "string" &&
      (sourceUrl.startsWith("/") || sourceUrl.startsWith("file:"))
    )
      return { ...raw, kind: raw.kind ?? "document", sourceUrl: null };
    return raw.kind === undefined ? { ...raw, kind: "document" } : raw;
  },
  z.discriminatedUnion("kind", [DocumentCitationSchema, MemoryCitationSchema, WebCitationSchema]),
);
export const MessageSchema = z.object({
  id,
  sessionId: id,
  role: z.string().max(32),
  content: z.string().max(2_000_000),
  citations: z.array(CitationSchema),
  generationStatus: z.string().max(64),
  createdAt: timestamp,
});

const eventEnvelopeShape = z.object({
  requestId: id,
  type: z.enum(["progress", "delta", "citations", "done", "error"]),
  sequence: z.number().int().nonnegative(),
  payload: z.record(z.unknown()),
});

export const BackendEventEnvelopeSchema = z.preprocess(
  (value) => {
    if (!value || typeof value !== "object") return value;
    const raw = value as Record<string, unknown>;
    return raw.requestId === undefined && raw.request_id !== undefined
      ? { ...raw, requestId: raw.request_id }
      : raw;
  },
  eventEnvelopeShape.extend({ requestId: z.string().min(1).max(100) }),
);
export const EventEnvelopeSchema = eventEnvelopeShape;

export const StagedSourceSchema = z.object({
  stagedSourceId: id,
  kind: z.literal("staged_file"),
  name: text(255),
  mediaType: z.string().max(255),
  sizeBytes: z.number().int().nonnegative(),
});

/**
 * One importable document format, as declared by the backend.
 *
 * The file dialog's filters, its size ceiling and the label on the import
 * button all come from this, so a format the backend can parse can be picked
 * without a matching edit here. That is what makes a format plugin usable
 * rather than merely installed.
 */
export const SourceFormatSchema = z.object({
  name: z.string().min(1).max(64),
  label: z.string().min(1).max(64),
  extensions: z.array(z.string().min(2).max(16)).min(1),
  mediaType: z.string().min(1).max(255),
  maxBytes: z.number().int().positive(),
});
export const StagedCollectionSchema = z.object({
  collectionId: id,
  displayName: text(255),
  itemCount: z.number().int().min(0).max(1000),
  totalBytes: z
    .number()
    .int()
    .nonnegative()
    .max(2 * 1024 ** 3),
});
export const BatchImportSchema = z.object({
  id,
  sourceKind: z.enum(["staged_directory", "web", "remote_repository", "search_results"]),
  repositoryId: id,
  state: z.enum([
    "discovering",
    "awaiting_confirmation",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
  ]),
  discoveryVersion: z.number().int().positive(),
  totalCount: z.number().int().nonnegative(),
  selectedCount: z.number().int().nonnegative(),
  completedCount: z.number().int().nonnegative(),
  failedCount: z.number().int().nonnegative(),
  skippedCount: z.number().int().nonnegative(),
  progress: z.number().int().min(0).max(100),
  message: z.string(),
  errorCode: z.string().nullable(),
  errorMessage: z.string().nullable(),
  retryable: z.boolean(),
  cancelRequested: z.boolean(),
  createdAt: timestamp,
  startedAt: timestamp.nullable(),
  completedAt: timestamp.nullable(),
  updatedAt: timestamp,
  lastEventSequence: z.number().int().nonnegative().optional(),
});
export const BatchItemSchema = z.object({
  id,
  batchId: id,
  ordinal: z.number().int().nonnegative(),
  title: text(240),
  displayPath: text(4_000),
  mediaType: z.string(),
  sizeBytes: z.number().int().nonnegative(),
  sourceRevision: z.string().min(1),
  allowedActions: z.array(z.enum(["create", "update", "attach_remote", "skip"])),
  selected: z.boolean(),
  decision: z.enum(["create", "update", "attach_remote", "skip"]).nullable(),
  state: z.enum(["discovered", "queued", "running", "completed", "skipped", "failed", "cancelled"]),
  importJobId: id.nullable(),
  errorCode: z.string().nullable(),
  errorMessage: z.string().nullable(),
  retryable: z.boolean(),
});
export const BatchProgressPayloadSchema = z.object({
  progress: z.number().int().min(0).max(100),
  state: z.enum([
    "discovering",
    "awaiting_confirmation",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
  ]),
  message: z.string(),
  stage: z.enum(["discovering", "awaiting_confirmation", "running", "paused"]),
  counts: z.object({
    total: z.number().int().nonnegative(),
    selected: z.number().int().nonnegative(),
    completed: z.number().int().nonnegative(),
    failed: z.number().int().nonnegative(),
    skipped: z.number().int().nonnegative(),
  }),
  itemId: id.nullable(),
  itemState: z
    .enum(["discovered", "queued", "running", "completed", "skipped", "failed", "cancelled"])
    .nullable(),
});
export const BatchItemPageSchema = z.object({
  items: z.array(BatchItemSchema),
  nextCursor: z.string().nullable(),
});
/**
 * 批量导入入参。后端 `CreateBatchRequest`（app/schemas/batches.py）是
 * 以 `kind` 为判别式的四路 union，四个变体的字段与约束必须逐字对齐：
 *
 * | kind                 | 字段 |
 * |---------------------|------|
 * | staged_directory    | sourceId |
 * | web                 | entryUrl / maxDepth(0-5) / maxPages(1-200) / useSitemap |
 * | remote_repository   | — |
 * | search_results      | searchRunId / resultIds(1-10) |
 *
 * 🔴 此前这里只写了 `staged_directory` 一种，而 UI 上「网站 / 远程知识库」
 * 两个 tab 会构造另外两种 —— preload 的 `CreateBatchInputSchema.parse`
 * 必然抛 ZodError，两个 tab 完全不可用。`kind` 一变，字段集就变，
 * 所以必须是 discriminatedUnion，不能用可选字段堆在一个 object 上。
 */
export const CreateBatchInputSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("staged_directory"), sourceId: id, repositoryId: id }),
  z.object({
    kind: z.literal("web"),
    entryUrl: z.string().url(),
    repositoryId: id,
    maxDepth: z.number().int().min(0).max(5).optional(),
    maxPages: z.number().int().min(1).max(200).optional(),
    useSitemap: z.boolean().optional(),
  }),
  z.object({ kind: z.literal("remote_repository"), repositoryId: id }),
  z.object({
    kind: z.literal("search_results"),
    searchRunId: id,
    resultIds: z.array(id).min(1).max(10),
    repositoryId: id,
  }),
]);
export const ConfirmBatchInputSchema = z.object({
  discoveryVersion: z.number().int().positive(),
  items: z
    .array(
      z.object({ itemId: id, decision: z.enum(["create", "update", "attach_remote", "skip"]) }),
    )
    .max(1000),
});
export const RetryBatchInputSchema = z.object({ itemIds: z.array(id).max(1000).optional() });
export const ChatStreamInputSchema = z.object({
  requestId: id,
  sessionId: id,
  message: bounded(20_000),
  repositoryIds: z.array(id).min(1).max(100),
  webSearchPermission: z.enum(["inherit", "off", "explicit"]).default("inherit"),
});
export const ChatSearchInputSchema = z.object({
  sessionId: id,
  userMessageId: id,
  requestId: id,
  repositoryIds: z.array(id).min(1).max(100),
});
export const SearchResultSchema = z.object({
  id,
  rank: z.number().int().positive(),
  canonicalUrl: z.string().url(),
  title: text(512),
  snippet: z.string().max(10_000),
  content: z.string().max(50 * 1024),
});
export const WebSearchRunSchema = z.object({
  id,
  status: z.string().max(32),
  provider: z.string().max(32).nullable().optional(),
  errorCode: z.string().max(128).nullable().optional(),
  results: z.array(SearchResultSchema).max(10),
});
export const SearchImportInputSchema = z.object({
  repositoryId: id,
  resultIds: z.array(id).min(1).max(10),
});

const relativePath = z
  .string()
  .max(4_000)
  .refine(
    (value) =>
      !value.startsWith("/") &&
      !value.startsWith("~") &&
      !/^[A-Za-z]:[\\/]/.test(value) &&
      !value.split(/[\\/]/).includes(".."),
    "localPath must be a logical relative path",
  );
export const SessionMemorySummarySchema = z.object({
  id,
  sessionId: id,
  state: z.enum(["pending", "generating", "ready", "stale", "failed"]),
  content: z.string().max(200_000).nullable(),
  topics: z.array(z.string().max(512)).max(100),
  repositoryIds: z.array(id).max(100),
  errorCode: z.string().max(128).nullable(),
  retryable: z.boolean(),
});
export const DistillationEditSchema = z.object({
  title: text(512),
  content: bounded(200_000),
  keyPoints: z.array(z.string().max(2_000)).max(100),
});
export const DistillationTargetSchema = z.discriminatedUnion("target", [
  z.object({ target: z.literal("local") }),
  z.object({ target: z.literal("remote"), repositoryId: id }),
]);
export const DistillationViewSchema = z.object({
  id,
  sessionId: id.nullable(),
  title: text(512),
  content: bounded(200_000),
  keyPoints: z.array(z.string().max(2_000)).max(100),
  sources: z.array(z.record(z.unknown())).max(500),
  repositoryIds: z.array(id).max(100),
  state: z.enum(["generating", "draft", "saving", "saved", "saved_unindexed", "failed"]),
  storageTarget: z.enum(["local", "remote"]).nullable(),
  localPath: relativePath.nullable(),
  documentId: id.nullable(),
  remoteUrl: z.string().url().max(4_000).nullable(),
  errorCode: z.string().max(128).nullable(),
  retryable: z.boolean(),
  createdAt: timestamp,
  updatedAt: timestamp,
});
export const MemoryListInputSchema = z.object({
  repositoryIds: z.array(id).min(1).max(100),
  kind: z.enum(["session_summary", "distillation"]).optional(),
  cursor: z.string().max(1_000).nullable().optional(),
});
export const MemoryItemSchema = z.object({
  id,
  kind: z.enum(["session_summary", "distillation"]),
  title: text(512),
  excerpt: z.string().max(5_000),
  repositoryIds: z.array(id),
  sessionId: id.nullable(),
  sourceId: id,
});
export const MemoryItemPageSchema = z.object({
  items: z.array(MemoryItemSchema),
  nextCursor: z.string().nullable(),
});

export type ErrorBody = z.infer<typeof ErrorBodySchema>;
export type ErrorEnvelope = z.infer<typeof ErrorEnvelopeSchema>;
export type ModelSettingsInput = z.infer<typeof ModelSettingsInputSchema>;
export type RuntimeSettingsInput = z.infer<typeof RuntimeSettingsInputSchema>;
export type SettingsView = z.infer<typeof SettingsViewSchema>;
export type ConnectionTestResult = z.infer<typeof ConnectionTestResultSchema>;
export type ModelConnectionResult = z.infer<typeof ModelConnectionResultSchema>;
export type AvailableModel = z.infer<typeof AvailableModelSchema>;
export type ModelListProbe = z.infer<typeof ModelListProbeSchema>;
export type ModelListView = z.infer<typeof ModelListViewSchema>;
export type ModelStatus = z.infer<typeof ModelStatusSchema>;
export type RemoteStatus = z.infer<typeof RemoteStatusSchema>;
export type BrowserInstallResult = z.infer<typeof BrowserInstallResultSchema>;
export type RemoteProviderCapabilities = z.infer<typeof RemoteProviderCapabilitiesSchema>;
export type RemoteProviderSummary = z.infer<typeof RemoteProviderSummarySchema>;
export type RemoteCredentialChannel = z.infer<typeof RemoteCredentialChannelSchema>;
export type RemoteCredentialTestResult = z.infer<typeof RemoteCredentialTestResultSchema>;
export type SaveRemoteCredentialInput = z.infer<typeof SaveRemoteCredentialInputSchema>;
export type PluginManifest = z.infer<typeof PluginManifestSchema>;
export type PluginDiagnostic = z.infer<typeof PluginDiagnosticSchema>;
export type PluginDirectory = z.infer<typeof PluginDirectorySchema>;
export type Repository = z.infer<typeof RepositorySchema>;
export type CreateRepositoryInput = z.infer<typeof CreateRepositoryInputSchema>;
export type UpdateRepositoryInput = z.infer<typeof UpdateRepositoryInputSchema>;
export type SyncOutcome = z.infer<typeof SyncOutcomeSchema>;
export type SyncStatus = z.infer<typeof SyncStatusSchema>;
export type ConflictView = z.infer<typeof ConflictViewSchema>;
export type ConflictResolution = z.infer<typeof ConflictResolutionSchema>;
export type DocumentVersion = z.infer<typeof DocumentVersionSchema>;
export type GraphNode = z.infer<typeof GraphNodeSchema>;
export type GraphEdge = z.infer<typeof GraphEdgeSchema>;
export type DocumentInput = z.infer<typeof DocumentInputSchema>;
export type DocumentSummary = z.infer<typeof DocumentSummarySchema>;
export type DocumentDetail = z.infer<typeof DocumentDetailSchema>;
export type DocumentOriginalChunk = z.infer<typeof DocumentOriginalChunkSchema>;
export type SourceRef = z.infer<typeof SourceRefSchema>;
export type SourcePreview = z.infer<typeof SourcePreviewSchema>;
export type CreateImportInput = z.infer<typeof CreateImportInputSchema>;
export type ImportJob = z.infer<typeof ImportJobSchema>;
export type SessionSummary = z.infer<typeof SessionSummarySchema>;
export type CreateSessionInput = z.infer<typeof CreateSessionInputSchema>;
export type Citation = z.infer<typeof CitationSchema>;
export type Message = z.infer<typeof MessageSchema>;
export type EventEnvelope = z.infer<typeof EventEnvelopeSchema>;
export type BackendEventEnvelope = z.infer<typeof BackendEventEnvelopeSchema>;
export type StagedSource = z.infer<typeof StagedSourceSchema>;
export type StagedCollection = z.infer<typeof StagedCollectionSchema>;
export type SourceFormat = z.infer<typeof SourceFormatSchema>;
export type BatchImport = z.infer<typeof BatchImportSchema>;
export type BatchItem = z.infer<typeof BatchItemSchema>;
export type BatchProgressPayload = z.infer<typeof BatchProgressPayloadSchema>;
export type BatchItemPage = z.infer<typeof BatchItemPageSchema>;
export type CreateBatchInput = z.infer<typeof CreateBatchInputSchema>;
export type ConfirmBatchInput = z.infer<typeof ConfirmBatchInputSchema>;
export type RetryBatchInput = z.infer<typeof RetryBatchInputSchema>;
export type ChatStreamInput = z.input<typeof ChatStreamInputSchema>;
export type ChatSearchInput = z.infer<typeof ChatSearchInputSchema>;
export type WebSearchSettingsInput = z.infer<typeof WebSearchSettingsInputSchema>;
export type WebSearchRun = z.infer<typeof WebSearchRunSchema>;
export type SearchImportInput = z.infer<typeof SearchImportInputSchema>;
export type SessionMemorySummary = z.infer<typeof SessionMemorySummarySchema>;
export type Distillation = z.infer<typeof DistillationViewSchema>;
export type DistillationEdit = z.infer<typeof DistillationEditSchema>;
export type DistillationTarget = z.infer<typeof DistillationTargetSchema>;
export type MemoryListInput = z.infer<typeof MemoryListInputSchema>;
export type MemoryItemPage = z.infer<typeof MemoryItemPageSchema>;

export type BatchProgressCounts = Pick<
  BatchImport,
  "totalCount" | "selectedCount" | "completedCount" | "failedCount" | "skippedCount"
>;

export interface StreamSubscription {
  requestId: string;
  cancel(): void;
  detach(): void;
}

export interface SourcesApi {
  stageDirectory(): Promise<StagedCollection | null>;
  /**
   * Formats the backend can import as a single file.
   *
   * The import dialog is built from this list, so a newly installed format
   * plugin becomes pickable without the renderer knowing its name.
   */
  listFormats(): Promise<SourceFormat[]>;
}
export interface BatchesApi {
  create(input: CreateBatchInput): Promise<BatchImport>;
  get(batchId: string): Promise<BatchImport>;
  list(): Promise<BatchImport[]>;
  listItems(batchId: string, cursor?: string | null): Promise<BatchItemPage>;
  confirm(batchId: string, input: ConfirmBatchInput): Promise<BatchImport>;
  cancel(batchId: string): Promise<BatchImport>;
  continue(batchId: string): Promise<BatchImport>;
  retry(batchId: string, input?: RetryBatchInput): Promise<BatchImport>;
  subscribe(
    batchId: string,
    afterSequence: number,
    onEvent: (event: EventEnvelope) => void,
  ): StreamSubscription;
}

export interface DocMindApi {
  localModel: {
    status(): Promise<z.infer<typeof LocalModelStatusSchema>>;
    models(): Promise<z.infer<typeof LocalModelsSchema>>;
  };
  settings: {
    get(): Promise<SettingsView>;
    saveModel(input: ModelSettingsInput): Promise<SettingsView>;
    testModel(): Promise<ModelConnectionResult>;
    listModels(input?: ModelListProbe): Promise<ModelListView>;
    skipModelSetup(): Promise<SettingsView>;
    clearDiagnostics(): Promise<void>;
    saveWebSearch(input: WebSearchSettingsInput): Promise<SettingsView>;
    saveRuntime(input: z.infer<typeof RuntimeSettingsInputSchema>): Promise<SettingsView>;
  };
  embedding: {
    status(): Promise<ModelStatus>;
    prepare(): Promise<ModelStatus>;
  };
  remote: {
    status(provider: string): Promise<RemoteStatus>;
    login(provider: string): Promise<RemoteStatus>;
    installBrowser(provider: string): Promise<BrowserInstallResult>;
    listProviders(): Promise<RemoteProviderSummary[]>;
    listCredentials(provider: string): Promise<RemoteCredentialChannel[]>;
    saveCredential(
      provider: string,
      channel: string,
      input: SaveRemoteCredentialInput,
    ): Promise<RemoteCredentialChannel>;
    testCredential(provider: string, channel: string): Promise<RemoteCredentialTestResult>;
    deleteCredential(provider: string, channel: string): Promise<RemoteCredentialChannel>;
  };
  /**
   * The plugin catalogue. Read-only and vendor-agnostic: the response is
   * derived from the backend's provider registry, so a newly installed
   * third-party plugin shows up here with no client change. `query` is matched
   * server-side against each plugin's own declared text; empty returns all.
   */
  plugins: {
    list(query?: string): Promise<PluginManifest[]>;
    diagnostics(): Promise<PluginDiagnostic[]>;
    /** The directory a plugin is installed by putting it in. */
    directory(): Promise<PluginDirectory>;
  };
  repositories: {
    list(): Promise<Repository[]>;
    create(input: CreateRepositoryInput): Promise<Repository>;
    update(repositoryId: string, input: UpdateRepositoryInput): Promise<Repository>;
  };
  sync: {
    get(repositoryId: string): Promise<SyncStatus>;
    trigger(repositoryId: string): Promise<SyncOutcome>;
  };
  conflicts: {
    list(repositoryId: string): Promise<ConflictView[]>;
    resolve(documentId: string, resolution: ConflictResolution): Promise<void>;
  };
  versions: {
    list(documentId: string): Promise<DocumentVersion[]>;
  };
  graph: {
    nodes(): Promise<GraphNode[]>;
    adjacency(nodeId: string): Promise<GraphEdge[]>;
  };
  documents: {
    list(repositoryId: string): Promise<DocumentSummary[]>;
    read(documentId: string): Promise<DocumentDetail>;
    /**
     * 读取原件的一段字节（含 begin 与 end）。
     * 阅读器按需分页依赖它，所以必须支持任意区间而不是整包返回。
     */
    readOriginalChunk(
      documentId: string,
      begin: number,
      end: number,
    ): Promise<DocumentOriginalChunk>;
    create(repositoryId: string, input: DocumentInput): Promise<DocumentDetail>;
    update(documentId: string, input: DocumentInput): Promise<DocumentDetail>;
    delete(documentId: string, confirm: true): Promise<void>;
  };
  imports: {
    inspect(input: SourceRef): Promise<SourcePreview>;
    create(input: CreateImportInput): Promise<ImportJob>;
    get(jobId: string): Promise<ImportJob>;
    retry(jobId: string): Promise<ImportJob>;
    cancel(jobId: string): Promise<ImportJob>;
    subscribe(
      jobId: string,
      afterSequence: number,
      onEvent: (event: EventEnvelope) => void,
    ): StreamSubscription;
  };
  chat: {
    listSessions(): Promise<SessionSummary[]>;
    createSession(input: CreateSessionInput): Promise<SessionSummary>;
    listMessages(sessionId: string): Promise<Message[]>;
    stream(
      input: ChatStreamInput,
      onEvent: (event: EventEnvelope) => void,
      afterSequence?: number,
    ): StreamSubscription;
    searchStream(
      input: ChatSearchInput,
      onEvent: (event: EventEnvelope) => void,
      afterSequence?: number,
    ): StreamSubscription;
  };
  webSearch: {
    getRun(runId: string, sessionId: string): Promise<WebSearchRun>;
    createImportBatch(runId: string, input: SearchImportInput): Promise<BatchImport>;
  };
  memory: {
    endSession(sessionId: string): Promise<SessionSummary>;
    deleteSession(sessionId: string, confirm: true): Promise<void>;
    getSummary(sessionId: string): Promise<SessionMemorySummary | null>;
    regenerateSummary(sessionId: string): Promise<SessionMemorySummary>;
    deleteSummary(sessionId: string): Promise<void>;
    createDistillation(sessionId: string): Promise<Distillation>;
    getDistillation(id: string): Promise<Distillation>;
    updateDistillation(id: string, input: DistillationEdit): Promise<Distillation>;
    regenerateDistillation(id: string): Promise<Distillation>;
    saveDistillation(id: string, input: DistillationTarget): Promise<Distillation>;
    deleteDistillation(id: string): Promise<void>;
    list(input: MemoryListInput): Promise<MemoryItemPage>;
    subscribeDistillation(
      id: string,
      afterSequence: number,
      onEvent: (event: EventEnvelope) => void,
    ): StreamSubscription;
  };
  dialogs: {
    /**
     * Open the file picker for one importable format and stage the choice.
     *
     * Takes the format's declared name rather than a fixed list of kinds: the
     * main process asks the backend for that format's extensions and size
     * ceiling, so the set of pickable formats is whatever the backend can
     * parse. A closed union here would silently exclude every plugin format.
     */
    chooseSource(format: string): Promise<StagedSource | null>;
  };
  sources: SourcesApi;
  batches: BatchesApi;
  shell: { openExternal(url: string): Promise<void> };
  /** Full app restart — the only recovery when a schema mismatch keeps failing. */
  app: { restart(): Promise<void> };
}

export const schemas = {
  SettingsViewSchema,
  ConnectionTestResultSchema,
  ModelConnectionResultSchema,
  AvailableModelSchema,
  ModelListViewSchema,
  ModelStatusSchema,
  RemoteStatusSchema,
  BrowserInstallResultSchema,
  RemoteProviderSummarySchema,
  RemoteCredentialChannelSchema,
  RemoteCredentialTestResultSchema,
  SaveRemoteCredentialInputSchema,
  RepositorySchema,
  UpdateRepositoryInputSchema,
  DocumentSummarySchema,
  DocumentDetailSchema,
  DocumentOriginalChunkSchema,
  SourcePreviewSchema,
  ImportJobSchema,
  SessionSummarySchema,
  MessageSchema,
  EventEnvelopeSchema,
  BackendEventEnvelopeSchema,
  StagedSourceSchema,
  SourceFormatSchema,
  StagedCollectionSchema,
  PluginDiagnosticSchema,
  PluginDirectorySchema,
  BatchImportSchema,
  BatchItemSchema,
  BatchProgressPayloadSchema,
  BatchItemPageSchema,
  SessionMemorySummarySchema,
  DistillationViewSchema,
  MemoryItemPageSchema,
};
