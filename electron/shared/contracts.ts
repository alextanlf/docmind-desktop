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

/**
 * 自定义供应商的预设名：`custom:<档案 id>`。
 *
 * 内置厂商是固定枚举，用户自建的厂商是一张可以增删的列表 —— 只有把厂商 id 编进
 * 预设名，第二家自定义厂商才有自己的配置和钥匙串槽位。`custom` 这个裸值只剩
 * 升级前的老配置在用，后端读到它时会归到固定的 `default` 那条档案上。
 */
export const CUSTOM_PRESET_PREFIX = "custom:";
/** 升级前那家「没有名字的自定义厂商」的 preset 字面量。只剩读老配置时用。 */
export const LEGACY_CUSTOM_PRESET = "custom";
/** 没有档案行的那家「未命名自定义」的固定 id，与后端 DEFAULT_CUSTOM_PROVIDER_ID 一致。 */
export const DEFAULT_CUSTOM_PROVIDER_ID = "default";
/** 表单「新建」用的占位 id：保存时后端换成随机 id，绝不落库。 */
export const NEW_CUSTOM_PROVIDER_ID = "new";
/** 自定义供应商没填名字时的兜底显示名，与后端 DEFAULT_CUSTOM_LABEL 一致。 */
export const DEFAULT_CUSTOM_LABEL = "自定义";

export const customPreset = (providerId: string) => `${CUSTOM_PRESET_PREFIX}${providerId}`;
export const isCustomPreset = (preset: string) => preset.startsWith(CUSTOM_PRESET_PREFIX);
export const customProviderId = (preset: string): string | null =>
  isCustomPreset(preset) ? preset.slice(CUSTOM_PRESET_PREFIX.length) : null;

export const ModelSettingsInputSchema = z.object({
  // 不是 ModelPresetSchema：预设里还有 `custom:<供应商 id>`，枚举不完。
  preset: z.string().trim().min(1).max(32),
  // 编辑已有配置时带的档案 id。不带就是新增：后端会先按「供应商 + 模型」认一条
  // 已有的，所以同一个模型再存一次是编辑，不会攒出两行。
  profileId: z.string().min(1).max(20).optional(),
  reasoningEffort: z.string().max(32).default(""),
  baseUrl: z.string().max(500),
  model: z.string().max(200),
  timeoutSeconds: z.number().positive().max(300),
  apiKey: z.string().max(2_000).nullable().optional(),
  // 自定义供应商的显示名；内置预设留空（它们的名字在前端的内置表里）。
  label: z.string().max(60).default(""),
});
/** 档案 id：小写字母数字。删除 / 切换当前都按它寻址。 */
export const ModelProfileIdSchema = z
  .string()
  .regex(/^[a-z0-9]{1,20}$/, "档案 id 只能是小写字母或数字");
export const ModelSettingsViewSchema = z.object({
  reasoningEffort: z.string().max(32).default(""),
  preset: z.string(),
  baseUrl: z.string(),
  model: z.string(),
  timeoutSeconds: z.number(),
  label: z.string().max(60).default(""),
});

/**
 * 一条已配置的模型 —— 设置页列表里的一行。
 *
 * `preset` 是**供应商标识**（内置预设 id，或 `custom:<供应商 id>`），API Key 槽位
 * 按它分，所以同一家供应商下的两条配置共用一个槽位（key 属于厂商）。`id` 是这条
 * 配置自己的标识，编辑 / 删除 / 切换当前都按它寻址。
 *
 * `active`（应用此刻用的是它）与 `hasApiKey`（这个供应商槽位里有没有 key）是视图
 * 状态，不是落库字段：后端现算，写进存储只会在下次读出来时变成过期的副本。
 */
export const ConfiguredModelSchema = z.object({
  id: z.string().min(1).max(20),
  preset: z.string().min(1).max(32),
  // 自定义供应商的显示名；内置预设留空，名字在前端那张内置表里。
  label: z.string().max(60).default(""),
  baseUrl: z.string(),
  model: z.string(),
  timeoutSeconds: z.number(),
  reasoningEffort: z.string().default(""),
  active: z.boolean().default(false),
  hasApiKey: z.boolean().default(false),
});
export type ConfiguredModel = z.infer<typeof ConfiguredModelSchema>;
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
  // Per preset, not global: a DeepSeek key does not open Kimi. The form used a
  // single flag and so kept saying 「已安全保存」 after the user switched vendor.
  // `hasApiKey` survives for the saved preset (first-run gating).
  apiKeys: z.record(z.string(), z.boolean()).default({}),
  // 用户已配置的模型，一条一行 —— 设置页「对话模型」只显示这张表。它取代了原来
  // 那个「模型预设」下拉框：那里面既有内置厂商（可能一个都没配）又有自定义厂商
  // （可能配了好几条），用户看不出自己到底配了什么。
  models: z.array(ConfiguredModelSchema).default([]),
  dataPath: z.string(),
  screenshotCount: z.number().int().nonnegative(),
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
export const ModelConnectionResultSchema = z.object({
  connected: z.boolean(),
  latencyMs: z.number().int().nonnegative(),
});

/**
 * 一份还没保存的表单草稿。
 *
 * 「获取模型列表」和「测试连接」都带着它走：二级页面里正在编的是一个还没落库的
 * 模型，不把草稿送过去，两个按钮问的都是当前使用的那条配置，给出的答案与眼前
 * 表单无关。`preset` 必须在：没有它，后端只会回落到已保存预设的内置列表。
 */
export const ModelProbeSchema = z.object({
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
/**
 * The plugin a row belongs to.
 *
 * Every card names the plugin that provided it, because the plugin — not the
 * credential channel — is the unit a user can switch off or take out. Declared
 * `source` rather than inferred: an unfamiliar value must still render, for the
 * same reason an unknown card kind does.
 */
export const PluginOriginSchema = z.object({
  plugin: z.string(),
  source: z.string(),
  /** The directory, when the plugin is one; absent for a distribution. */
  path: z.string().nullable().optional(),
  version: z.string().nullable().optional(),
  /** False once the user switched it off. The row stays on the page. */
  enabled: z.boolean().default(true),
  /** True when it loaded. Enabled but inactive is what a load failure looks like. */
  active: z.boolean().default(true),
  error: z.string().nullable().optional(),
  /** Whether a switch and a remove button belong on the detail view. */
  toggleable: z.boolean().default(false),
  removable: z.boolean().default(false),
});

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
  /**
   * Which plugin this row belongs to. Absent only for a contribution installed
   * with nothing to attribute it to, which is not a shape the application
   * produces — treated as "no plugin actions" rather than guessed at.
   */
  origin: PluginOriginSchema.nullable().optional(),
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
 * The outcome of switching a plugin off or taking it out.
 *
 * `restartRequired` is stated by the server rather than assumed by the client:
 * plugins are discovered once per process, so nothing the user just changed is
 * live yet, and a page that stayed silent about that would look like a switch
 * that did nothing.
 */
export const PluginStateSchema = z.object({
  plugin: z.string(),
  enabled: z.boolean(),
  restartRequired: z.boolean(),
  /** Where a removed plugin was moved to; null for a switch. */
  removedTo: z.string().nullable().optional(),
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
 * 一个 URL 没被收录的原因。与后端 `CrawlRejectionReason`
 * （backend/app/schemas/batches.py）逐字对齐，两边的测试各守一半：
 * 后端断言"映射产生的每个值都在这份集合里"，前端断言"这份集合里每个值都有文案"。
 *
 * 🔴 分类必须区分处置方式不同的四件事：站点拒绝了你（换请求）、到不了（换来源）、
 * 地域限制（只能换来源）、我们主动跳过（不值得等）。合成一个"抓取失败"，用户就
 * 只能反复重试同一件注定失败的事。
 */
export const CrawlRejectionReasonSchema = z.enum([
  "robots_denied",
  "scope_rejected",
  "unsupported_media",
  "limit_reached",
  "unreachable",
  "throttled",
  "http_rejected",
  "geo_blocked",
  "tls_error",
  "proxy_error",
  "fetch_failed",
]);

export const BatchDiagnosticSchema = z.object({
  canonicalUrl: z.string(),
  reason: CrawlRejectionReasonSchema,
});

export const BatchDiagnosticsPageSchema = z.object({
  total: z.number().int().nonnegative(),
  /** 列表被上限截断 —— 界面必须能说出"还有多少条没列出来"。 */
  truncated: z.boolean(),
  items: z.array(BatchDiagnosticSchema),
});

export type CrawlRejectionReason = z.infer<typeof CrawlRejectionReasonSchema>;
export type BatchDiagnostic = z.infer<typeof BatchDiagnosticSchema>;
export type BatchDiagnosticsPage = z.infer<typeof BatchDiagnosticsPageSchema>;
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
export type ModelProbe = z.infer<typeof ModelProbeSchema>;
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
export type PluginOrigin = z.infer<typeof PluginOriginSchema>;
export type PluginState = z.infer<typeof PluginStateSchema>;
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
  /** 哪些页面没被收录、各自为什么。单独一次请求：它比批次快照大得多，也很少看。 */
  diagnostics(batchId: string): Promise<BatchDiagnosticsPage>;
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
    /** 删掉一条已配置的模型；它是这家供应商最后一条时，API Key 也一并删除。 */
    deleteModel(profileId: string): Promise<SettingsView>;
    /** 把某一条配置设为当前使用 —— 应用同一时间只用一个模型回答。 */
    activateModel(profileId: string): Promise<SettingsView>;
    /** 带草稿就测正在编的那一条；不带则测当前使用的那条。 */
    testModel(input?: ModelProbe): Promise<ModelConnectionResult>;
    listModels(input?: ModelProbe): Promise<ModelListView>;
    skipModelSetup(): Promise<SettingsView>;
    clearDiagnostics(): Promise<void>;
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
   * The plugin catalogue. Read-only apart from the switch and the remove
   * button: the rows are derived from what is installed, so a newly installed
   * third-party plugin shows up here with no client change. `query` is matched
   * server-side against each plugin's own declared text; empty returns all.
   */
  plugins: {
    list(query?: string): Promise<PluginManifest[]>;
    diagnostics(): Promise<PluginDiagnostic[]>;
    /** The directory a plugin is installed by putting it in. */
    directory(): Promise<PluginDirectory>;
    /**
     * Switch a plugin off, or back on. Takes effect at the next start —
     * plugins are discovered once per process — which the response says.
     */
    setEnabled(plugin: string, enabled: boolean): Promise<PluginState>;
    /**
     * Take a directory plugin out of the plugin directory. It is moved, not
     * deleted, and the response reports where it went.
     */
    uninstall(plugin: string): Promise<PluginState>;
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
