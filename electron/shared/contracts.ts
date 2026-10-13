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

/** 图片识别开关的入参。 */
export const FigureOcrInputSchema = z.object({ enabled: z.boolean() });

/** 图表语义描述开关的入参。 */
export const FigureDescriptionInputSchema = z.object({ enabled: z.boolean() });

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
  /**
   * 导入时是否识别图片里的文字。默认开。
   *
   * 关掉换速度（实测 0.11~0.19 秒/页），代价是**纯扫描页从索引里消失** —— 截图型幻灯片
   * 与扫描书没有文字层，识别是它们唯一的文字来源。所以这是要如实说明代价的开关。
   */
  figureOcr: z.boolean().default(true),
  /**
   * 是否用视觉模型描述图表语义。**默认关闭**。
   *
   * 打开它意味着把文档里的图片发到模型服务商 —— 与「本地优先」是相反的取舍，所以必须
   * 由用户显式打开。它补的是 OCR 读不到的那一类图（状态转移图、曲线图、示意图）。
   */
  figureDescription: z.boolean().default(false),
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
   * 这个来源是否报得出每篇文档的目录路径。声明它只是为了让"provider 能做什
   * 么"在两侧一致；界面判断要不要画树用的是数据本身（有没有非空 path），
   * 那比声明更贴近眼前的这个知识库。
   */
  documentPaths: z.boolean(),
  /**
   * 这个来源能不能把改名写回远端。
   *
   * 声明为 false 时，界面在改名成功后必须提示"远端名字没跟着变" —— 远端绑定的
   * 知识库下一次列表刷新会把远端旧名字写回来，不说的话用户会以为改名没生效。
   *
   * 带默认值而不是必填：老版本后端不含这两个字段，缺了就当"改不了"，比让整个
   * provider 列表解析失败好。
   */
  repositoryRename: z.boolean().default(false),
  documentRename: z.boolean().default(false),
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
export const RenameRepositoryInputSchema = z.object({
  name: text(120),
});
/**
 * 一次改名到底改到了哪里。
 *
 * `local_only`：只有 DocMind 里的名字变了（本地知识库，或 provider 改不了远端名字）。
 * 后者下一次列表刷新会把远端名字拉回来，所以界面**必须**把这件事说出来，否则用户
 * 会以为远端也跟着改了。
 * `remote`：远端名字也一起改了。
 */
export const RenameScopeSchema = z.enum(["local_only", "remote"]);
export const RepositoryRenameResultSchema = z.object({
  scope: RenameScopeSchema,
  repository: RepositorySchema,
});
/**
 * 一个被用户从 DocMind 移除的远端知识库。
 *
 * 带 `name` 与 `provider`：用户要凭这两样判断"这个该不该恢复"。只有两个 id 的话，
 * 「已移除」列表就只是一串看不懂的标识符，用户没法决定。
 */
export const RemovedRepositorySchema = z.object({
  id,
  provider: z.string().max(32),
  remoteId: z.string().max(255),
  name: text(120),
  removedAt: timestamp,
});

export const DocumentInputSchema = z.object({
  title: text(240),
  content: bounded(2_000_000),
});
export const RenameDocumentInputSchema = z.object({
  title: text(240),
});
export const DocumentSummarySchema = z.object({
  id,
  repositoryId: id,
  remoteId: z.string().nullable().optional(),
  title: text(240),
  remoteUrl: z.string().max(4_000).nullable().optional(),
  /**
   * 远端目录路径（逐级名字）。空数组 = 根目录，本地文档恒为空数组。
   *
   * 只有声明了 `documentPaths` 能力的 provider 才有实际内容 —— 界面据此决定
   * 显示树还是平铺，而不是自己猜一层层级出来。
   */
  path: z.array(z.string().max(512)).default([]),
  chunkCount: z.number().int().nonnegative(),
  status: z.string(),
  remoteDeleted: z.boolean(),
  createdAt: timestamp,
  updatedAt: timestamp,
});

export const DocumentRemovalResultSchema = z.object({
  removed: z.number().int().nonnegative(),
});

export const DocumentArchiveResultSchema = z.object({
  archived: z.number().int().nonnegative(),
});

/**
 * 一篇已归档的文档。
 *
 * 带 `repositoryName` 与 `archivedAt`：这份列表住在设置页，用户要在**跨知识库**
 * 的一堆条目里认出想恢复的那一篇，只有 id 和标题是不够的。`remoteId` 决定「永久
 * 删除」里那个"连远端原文一起删"的选项该不该出现 —— 本地文档没有远端原文可删。
 */
export const ArchivedDocumentSchema = z.object({
  id,
  repositoryId: id,
  repositoryName: text(240),
  title: text(240),
  remoteId: z.string().max(255).nullable(),
  archivedAt: timestamp,
});

/**
 * 一篇被移出 DocMind 的远端文档。
 *
 * 带 `title`：用户要凭它判断该不该恢复；只给一个远端 id 的话，这个列表就只是
 * 一串看不懂的标识符。
 */
export const RemovedDocumentSchema = z.object({
  id,
  repositoryId: id,
  remoteId: z.string().max(255),
  title: text(240),
  /** 跨知识库那份列表要显示它，用户才知道该去哪个库里找这篇文档。 */
  repositoryName: text(240),
  removedAt: timestamp,
});

export const RemovedDocumentRestoreSchema = z.object({
  removalId: id,
  repositoryId: id,
  remoteId: z.string().max(255),
  title: text(240),
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
/**
 * 一篇文档里的一张插图。图的内容（图内文字、识别结果）已经在正文里参与检索；这里带的
 * 是展示所需的部分，其中 `bbox` 让客户端能在 PDF 原件上框出这张图。
 */
/**
 * 插图文件名。与后端 `_ASSET_NAME` 的白名单一致（64 位十六进制摘要 + .png）。
 *
 * 渲染层传上来的名字一律先过这里再拼进 URL：文件名直接来自后端的插图清单，但把它当作
 * 可信输入是没必要的 —— 一旦它被污染，拼出来的就是任意路径。
 */
export const DocumentAssetNameSchema = z.string().regex(/^[0-9a-f]{64}\.png$/);

export const DocumentFigureSchema = z.object({
  asset: z.string().regex(/^[0-9a-f]{64}\.png$/),
  pageNumber: z.number().int().positive().nullable().default(null),
  caption: z.string().default(""),
  width: z.number().int().nonnegative().default(0),
  height: z.number().int().nonnegative().default(0),
  bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable().default(null),
  fullPage: z.boolean().default(false),
});

export const DocumentDetailSchema = DocumentSummarySchema.extend({
  content: z.string().max(2_000_000),
  /**
   * 原件预览能力。为 null 表示磁盘上没有可识别的原件（远程文档、原件被删），
   * 渲染层据此隐藏「原文」视图 —— 不要靠试错去发现。
   */
  originalMediaType: z.string().max(255).nullable(),
  originalByteSize: z.number().int().nonnegative().nullable(),
  /**
   * 原件状态。`missing` 与 `none` 分开是刻意的：合成一个值，界面就只能说"没有原文"，
   * 用户分不清是自己没导入原件，还是文件被挪走了（后者他能修）。
   *
   * 后端每次读文档时 `stat` 原件算出来，不落库 —— 存下来的状态列必然会在某一刻过期。
   */
  originalState: z.enum(["none", "available", "changed", "missing"]).default("none"),
  /** 没有图是常态，后端给空数组，客户端不必先判空再遍历。 */
  figures: DocumentFigureSchema.array().default([]),
});

/** 改名的结果 + 这个词条现在的样子（见 `RenameScopeSchema` 的说明）。 */
export const DocumentRenameResultSchema = z.object({
  scope: RenameScopeSchema,
  document: DocumentDetailSchema,
});

/**
 * 原件分片。二进制以 base64 过 IPC：`contextBridge` 不保证透传 TypedArray，
 * 而本项目已有同款约定（见后端 `_document_snapshot` 的 `file_bytes_b64`）。
 */
/**
 * "任意受支持格式"的哨兵：传给 `dialogs.chooseSource` 表示"过滤器把所有格式都列上"。
 *
 * 用在**重新定位原件**上：那时原件可能已经不在磁盘上了（这正是最常见的情形），
 * 文档记录里连媒体类型都没有，界面无从知道它原本是什么格式。
 */
export const ANY_FORMAT = "*";

/**
 * 重新定位原件：只改绑定，不转换。
 *
 * `pathToken` 与导入时同一条约束 —— 由**主进程**在转发前补上（见 `ipc-handlers.ts`），
 * 渲染层拿不到也造不出来；否则"重新定位"就成了绕开导入那道门、把文档指到任意文件上的路。
 */
export const RelinkOriginalInputSchema = z.object({
  path: bounded(4_000),
  pathToken: bounded(255).nullable().optional(),
});

export const DocumentOriginalChunkSchema = z.object({
  data: z.string(),
  total: z.number().int().nonnegative(),
});

/**
 * 导入来源的类别。**与后端 `app/schemas/imports.py` 的 `SOURCE_KINDS` 是一对**，
 * `contracts.test.ts` 会去读那份 Python 源码逐字比对 —— 两边各说各话的代价不是报错，
 * 而是运行时才炸的 ValidationError（`linked_file` 加进来时后端就真的踩过一次：
 * `SourceRef` 与 `SourcePreview` 各自声明了一份枚举，只改了一处）。
 */
export const SOURCE_KINDS = ["url", "staged_file", "linked_file"] as const;

export const SourceRefSchema = z.object({
  kind: z.enum(SOURCE_KINDS),
  /**
   * url 是地址；staged_file 是暂存标识（UUID）；linked_file 是**用户磁盘上的绝对路径**。
   *
   * 路径由主进程在转发时签名（`path-token` 字段），渲染层拿不到也造不出来 ——
   * 所以"渲染层不能指名任意文件"这条性质在链接型导入下依然成立。
   */
  value: bounded(4_000),
  /**
   * 用户在选择器里看到的文件名（`报告.pdf`）。
   *
   * 暂存副本按 UUID 命名（防路径注入），后端因此拿不到这个名字；上游自身不带标题的
   * 格式（PDF、docx）失去它就只剩 UUID 当标题。只用于显示：后端只取 basename。
   *
   * **必须是 `.nullable().optional()`**：后端 `SourceRef.name` 是可空字段，且
   * FastAPI 不带 `exclude_none`，所以 URL 导入与老记录的响应里是显式的
   * `"name": null` —— 只写 `.optional()` 会放过缺席、卡住 null，整个导入进度接口
   * 在 IPC 边界被拒（vitest 里的假 API 不带这个键，抓不到）。
   */
  name: bounded(255).nullable().optional(),
  /**
   * 路径签名，**只由主进程填**（IPC 处理器在转发给后端之前加上）。
   *
   * 后端只读签名对得上的路径；后端回显任务状态时也会把它清掉，所以渲染层从头到尾
   * 见不到任何一个 token。这里声明它只是因为两个方向共用一份 schema。
   */
  pathToken: bounded(255).nullable().optional(),
});
export const SourcePreviewSchema = z.preprocess(
  (value) => {
    if (!value || typeof value !== "object") return value;
    const raw = value as Record<string, unknown>;
    if (raw.sourceKind === "staged_file" || raw.sourceKind === "linked_file")
      return { ...raw, sourceUrl: null };
    return raw;
  },
  z.object({
    title: text(240),
    sourceKind: z.enum(SOURCE_KINDS),
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

/**
 * 用户磁盘上的原件：对话框返回的**路径**，不复制任何字节。
 *
 * 渲染层把 `path` 原样送回 `imports.inspect` / `imports.create`，主进程在那两个处理器里
 * 补上签名（见 `ipc-handlers.ts` 的 `withSignedPath`）—— 于是渲染层从不需要接触签名，
 * 也无法凭空造出一个可用路径。
 */
export const LinkedSourceSchema = z.object({
  kind: z.literal("linked_file"),
  path: bounded(4_000),
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

/**
 * 原件在用户磁盘上的位置 —— **只在主进程内部流转**。
 *
 * 它不进 `DocMindApi`：渲染层既拿不到 `path`，也拿不到 `pathToken`。
 */
export const OriginalLocationSchema = z.object({
  path: z.string().max(4_000).nullable(),
  pathToken: z.string().max(200).nullable(),
  state: z.enum(["none", "available", "changed", "missing"]),
});

/**
 * 目录扫描的进度。
 *
 * `total` 来自遍历目录项（不读文件内容），所以它在第一个文件被读完之前就是准的 ——
 * 界面因此能显示"已扫描 12/300"，而不是一个转不完的圈。
 */
export const ScanProgressPayloadSchema = z.object({
  scanned: z.number().int().nonnegative(),
  total: z.number().int().nonnegative(),
});

/** 扫描成功时的落点：一份清单（`StagedCollection`），或者"用户取消了"。 */
export const ScanResultPayloadSchema = z.object({
  collection: StagedCollectionSchema.nullable(),
  cancelled: z.boolean().optional(),
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

/**
 * 网页语料：该抓哪些站点。与后端 `app/schemas/corpus.py` 逐字对齐。
 *
 * `signals` 与 `evidence` 是这个功能的核心 —— 一条候选没有理由就等于让用户猜；
 * `verified` 把"我们核对过的种子表"与"模型的一家之言"分开，两者可信度不同。
 */
export const CorpusCandidateSchema = z.object({
  domain: z.string(),
  signals: z.array(z.string()),
  evidence: z.array(z.string()),
  label: z.string().nullable().optional(),
  kind: z.string().nullable().optional(),
  entryUrl: z.string().nullable().optional(),
  verified: z.boolean(),
  score: z.number().int(),
});

export const CorpusNominationSchema = z.object({
  attempted: z.boolean(),
  count: z.number().int().nonnegative(),
});

export const CorpusCandidatesResponseSchema = z.object({
  candidates: z.array(CorpusCandidateSchema),
  nomination: CorpusNominationSchema,
});

export const CorpusTopicSchema = z.object({
  key: z.string(),
  label: z.string(),
  /** 这个话题下有几个已核对的第一方来源 —— 用户点之前就知道能得到多少。 */
  sourceCount: z.number().int().nonnegative(),
});

export const CorpusTopicsResponseSchema = z.object({
  topics: z.array(CorpusTopicSchema),
});

export const CorpusCandidateRequestSchema = z.object({
  text: z.string().max(4000).optional(),
  /** 点选的话题 key（来自 `/api/corpus/topics`）：精确选取，不经过关键词匹配。 */
  topics: z.array(z.string()).max(20).optional(),
  explicitDomains: z.array(z.string()).max(50).optional(),
  documentUrls: z.array(z.string()).max(500).optional(),
  crawlUrls: z.array(z.string()).max(500).optional(),
  upstreamUrls: z.array(z.string()).max(500).optional(),
  nominate: z.boolean().optional(),
  nominateLimit: z.number().int().min(0).max(10).optional(),
  limit: z.number().int().min(1).max(50).optional(),
});

export const WebSourceInputSchema = z.object({
  domain: z.string().min(1).max(253),
  entryUrl: z.string().max(2000).optional(),
  label: z.string().max(200).optional(),
  kind: z.string().max(32).optional(),
  verified: z.boolean().optional(),
  evidence: z.array(z.string()).max(20).optional(),
});

export const SaveWebSourcesRequestSchema = z.object({
  sources: z.array(WebSourceInputSchema).min(1).max(50),
});

/** 进行中的进度。`budget` 一起给出，"已抓 7 页"才有参照。 */
export const CrawlProgressSchema = z.object({
  pages: z.number().int().nonnegative().default(0),
  updated: z.number().int().nonnegative().default(0),
  budget: z.number().int().nonnegative().default(0),
});

/** 上一次抓完的结果。 */
export const CrawlOutcomeSchema = z.object({
  /** 抓取之后的语料规模（不是本次下载了几页）。 */
  pages: z.number().int().nonnegative().default(0),
  chunks: z.number().int().nonnegative().default(0),
  /** 内容未变、没有重新切块与重写索引的页数 —— 增量刷新的收益体现在这里。 */
  keptPages: z.number().int().nonnegative().default(0),
  updatedPages: z.number().int().nonnegative().default(0),
  removedPages: z.number().int().nonnegative().default(0),
  skippedCount: z.number().int().nonnegative().default(0),
  /** 因为页数上限提前结束 —— 用户需要知道"这不是全部"。 */
  truncated: z.boolean().default(false),
});

export const WebSourceSchema = z.object({
  domain: z.string(),
  entryUrl: z.string().nullable().optional(),
  label: z.string().nullable().optional(),
  kind: z.string().nullable().optional(),
  verified: z.boolean(),
  evidence: z.array(z.string()),
  /** 语料规模与最近一次抓取。没有它们，一个一直抓失败的来源看起来和刚收录时一样。 */
  pageCount: z.number().int().nonnegative().default(0),
  chunkCount: z.number().int().nonnegative().default(0),
  lastCrawledAt: z.string().nullable().optional(),
  /** 上次失败的原因码 —— 与抓取诊断同一套取值，所以文案也只有一份。 */
  lastError: CrawlRejectionReasonSchema.nullable().optional(),
  /**
   * 抓取状态。"被中断"是独立取值：应用在抓取途中退出、或抓取内部出错时，用户该看到
   * 的是"这次没跑完"，而不是"从来没抓过"或"抓失败了"。
   */
  crawlState: z.enum(["idle", "running", "interrupted"]).default("idle"),
  progress: CrawlProgressSchema.nullable().optional(),
  /** 上一次抓完的结果。落库、长期展示 —— 一次性的提示刷新就没了。 */
  lastSummary: CrawlOutcomeSchema.nullable().optional(),
  addedAt: z.string(),
});

export const WebSourcesResponseSchema = z.object({
  sources: z.array(WebSourceSchema),
});

export const CrawlSourceResponseSchema = WebSourcesResponseSchema.extend({
  /** 任务这一次是否真的起来了。已经在抓时为 false —— 重复点击不该是错误。 */
  started: z.boolean(),
});

export const CancelCrawlResponseSchema = WebSourcesResponseSchema.extend({
  /** 取消时这个域名是否真的在抓。不在抓时返回 false —— 不是错误。 */
  cancelled: z.boolean(),
});

export type CorpusCandidate = z.infer<typeof CorpusCandidateSchema>;
export type CorpusTopic = z.infer<typeof CorpusTopicSchema>;
export type CorpusCandidateRequest = z.infer<typeof CorpusCandidateRequestSchema>;
export type CorpusCandidatesResponse = z.infer<typeof CorpusCandidatesResponseSchema>;
export type CorpusTopicsResponse = z.infer<typeof CorpusTopicsResponseSchema>;
export type WebSource = z.infer<typeof WebSourceSchema>;
export type WebSourceInput = z.infer<typeof WebSourceInputSchema>;
export type SaveWebSourcesRequest = z.infer<typeof SaveWebSourcesRequestSchema>;
export type WebSourcesResponse = z.infer<typeof WebSourcesResponseSchema>;
export type CrawlProgress = z.infer<typeof CrawlProgressSchema>;
export type CrawlOutcome = z.infer<typeof CrawlOutcomeSchema>;
export type CrawlSourceResponse = z.infer<typeof CrawlSourceResponseSchema>;
export type CancelCrawlResponse = z.infer<typeof CancelCrawlResponseSchema>;

/** 本地网页索引收录哪些站点。与后端 `/api/corpus` 对齐。 */
export interface CorpusApi {
  /** 推导候选来源：每条都带证据。提名不可用时仍会返回种子表的结果。 */
  candidates(input: CorpusCandidateRequest): Promise<CorpusCandidatesResponse>;
  /**
   * 种子表里的话题清单 —— 首次体验的入口。
   *
   * 新用户打开这一页时清单是空的，而"写下一个问题"要求他先知道自己想问什么。这些话题
   * 随应用分发、逐条带证据，点一下就能得到确定的第一方来源，既不用打字也不用等模型。
   */
  topics(): Promise<CorpusTopicsResponse>;
  sources(): Promise<WebSourcesResponse>;
  saveSources(input: SaveWebSourcesRequest): Promise<WebSourcesResponse>;
  /** 幂等：删一个本来就不存在的域名也返回成功，响应始终是删除后的真实清单。 */
  removeSource(domain: string): Promise<WebSourcesResponse>;
  /**
   * 起一个**后台**抓取任务。响应只说"起没起来"。
   *
   * 后台是必然的：同步执行的上限不是站点有多少页，而是用户愿意等多久 —— 那只能给
   * 20 页。进度与结果都落库，界面轮询来源清单即可。
   */
  crawlSource(domain: string): Promise<CrawlSourceResponse>;
  /**
   * 停止一个正在跑的抓取。`cancelled: false` 表示当时并没有在抓 —— 不是错误。
   *
   * 一个 200 页的抓取在慢站点上要好几分钟，用户必须能停。
   */
  cancelCrawl(domain: string): Promise<CancelCrawlResponse>;
}
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
export type RepositoryRenameResult = z.infer<typeof RepositoryRenameResultSchema>;
export type RemovedRepository = z.infer<typeof RemovedRepositorySchema>;
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
export type DocumentRenameResult = z.infer<typeof DocumentRenameResultSchema>;
export type DocumentRemovalResult = z.infer<typeof DocumentRemovalResultSchema>;
export type RemovedDocument = z.infer<typeof RemovedDocumentSchema>;
export type ArchivedDocument = z.infer<typeof ArchivedDocumentSchema>;
export type DocumentArchiveResult = z.infer<typeof DocumentArchiveResultSchema>;
export type RemovedDocumentRestore = z.infer<typeof RemovedDocumentRestoreSchema>;
export type DocumentOriginalChunk = z.infer<typeof DocumentOriginalChunkSchema>;
export type DocumentFigure = z.infer<typeof DocumentFigureSchema>;
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
export type LinkedSource = z.infer<typeof LinkedSourceSchema>;
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
  /**
   * 选一个目录并**在后台就地扫描**它，边扫边推进度。
   *
   * 立刻返回订阅句柄：`scanId` 由调用方生成（与聊天流同一套 `subscription` 机制），
   * 进度是 `progress` 事件、结果是 `done`（`collection` 为 `null` 表示用户取消），
   * `cancel()` 用来中止扫描。
   *
   * 从前这是一问一答的 `stageDirectory()`：扫完才返回，进度与取消都无从谈起。
   * 扫描要把每个文件完整读一遍算摘要，几百个文件就是几分钟。
   */
  scanDirectory(
    requestId: string,
    onEvent: (event: EventEnvelope) => void,
  ): { requestId: string; detach(): void; cancel(): void };
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
    setFigureOcr(enabled: boolean): Promise<SettingsView>;
    setFigureDescription(enabled: boolean): Promise<SettingsView>;
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
    /**
     * 给知识库改名。远端支持时一并回写远端，并回 `scope: "remote"`。
     *
     * `scope: "local_only"` 时必须提示用户"远端名字没跟着变" —— provider 改不了
     * 远端名字（飞书没有这个接口，语雀网页会话也没有），下一次列表刷新会把远端
     * 旧名字拉回来。
     */
    rename(repositoryId: string, name: string): Promise<RepositoryRenameResult>;
    /**
     * 从 DocMind 移除一个知识库。
     *
     * 远端（语雀 / 飞书）上的仓库与文档一律不动 —— 这里的语义是"从 DocMind
     * 移除"，不是"把你在别处的资料删掉"。远端绑定的库会留下一条移除记录，
     * 否则下一次刷新列表它就自己回来了。
     */
    remove(repositoryId: string, confirm: true): Promise<void>;
    listRemoved(): Promise<RemovedRepository[]>;
    /** 恢复一个被移除的远端知识库；文档要用「立即同步」再拉一次。 */
    restore(removalId: string): Promise<Repository>;
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
    /**
     * 读取一张插图的字节，返回 base64。
     *
     * 用 base64 而不是 Uint8Array，与 `readOriginalChunk` 同一条约定：
     * `contextBridge` 不保证透传 TypedArray。
     */
    readAsset(documentId: string, assetName: string): Promise<string>;
    create(repositoryId: string, input: DocumentInput): Promise<DocumentDetail>;
    update(documentId: string, input: DocumentInput): Promise<DocumentDetail>;
    /**
     * 只改标题，**不动正文**。
     *
     * 与 `update` 分开：那条是"编辑保存"（重新分块、重新嵌入、写版本、整篇回写远端）。
     * 改名只需要改一个字符串，走那条路等于为了改标题把用户没碰过的正文重做一遍。
     */
    rename(documentId: string, title: string): Promise<DocumentRenameResult>;
    /**
     * 归档一篇文档：**内容一概不动**，只让它从列表、检索、图谱、同步里消失。
     *
     * 这是默认动作，因为它是可撤销的：恢复是即时的（正文、向量、版本历史都还在）。
     */
    archive(documentId: string): Promise<void>;
    /** 取消归档。远端文档会重新参与同步。 */
    unarchive(documentId: string): Promise<void>;
    /** 所有知识库里已归档的文档（设置页那份列表）。 */
    listArchived(): Promise<ArchivedDocument[]>;
    /** 批量归档，返回真正归档的篇数。 */
    archiveMany(repositoryId: string, documentIds: string[]): Promise<DocumentArchiveResult>;
    /**
     * **永久**移出 DocMind —— 只删本地，远端原文不动。
     *
     * 不可撤销：正文、分块、向量、版本历史、图谱节点、磁盘目录一起删。归档之后的
     * 永久删除走这里；界面把它放在设置页的「已归档的文档」里，而不是文档列表上。
     */
    remove(documentId: string, confirm: true): Promise<void>;
    /** 删除文档**并且**删除远端原文。不可撤销。 */
    deleteRemote(documentId: string, confirm: true): Promise<void>;
    /** 批量移出（只删本地），返回真正移出的篇数。不可撤销。 */
    removeMany(repositoryId: string, documentIds: string[]): Promise<DocumentRemovalResult>;
    /**
     * 旧流程留下的「已移除、可重新同步」的远端文档（跨知识库）。
     *
     * 与「已归档」分开一趟请求：归档的文档行还在，恢复是即时的；这批的文档行当时
     * 就被删了，恢复要重新下载并重建索引。
     */
    listRemoved(): Promise<RemovedDocument[]>;
    /**
     * 恢复一篇被移出的远端文档。
     *
     * 只负责"允许它回来"（删掉移出记录并清掉陈旧同步快照），文档内容由紧接的
     * 一次同步重新下载并重建索引。
     */
    restoreRemoved(repositoryId: string, removalId: string): Promise<RemovedDocumentRestore>;
    /**
     * 把文档重新绑到磁盘上的另一个路径（文件被挪走/换了外接盘之后）。
     *
     * 只改绑定：如果新文件的 mtime/size 与当初记录的一致（改名、移动），状态会自动回到
     * `available`；换成别的文件则显示 `changed` 并提示重新转换。转换是重操作，
     * 由用户显式点「重新转换」触发。
     */
    relinkOriginal(documentId: string, path: string): Promise<DocumentDetail>;
    /** 重新读取原件并转换一次（用户改了原件之后）。复用编辑保存那条流水线。 */
    reconvert(documentId: string): Promise<DocumentDetail>;
    /**
     * 用系统程序打开原件（Word、Preview……）。
     *
     * 只传 `documentId`：路径由主进程向后端要、**验签之后**才落到文件系统上 —— 渲染层
     * 从头到尾看不到它。原件不在原处时抛 `DOCUMENT_ORIGINAL_MISSING`，界面上的出口是
     * 「重新定位原件」。
     */
    openOriginal(documentId: string): Promise<void>;
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
     * Open the file picker for one importable format and return the choice.
     *
     * **不复制字节**：返回的是用户在磁盘上的那个路径（`LinkedSource`），解析与原件视图
     * 都直接读它。目录批次也一样（清单里只记相对路径 + sha256，见
     * `docs/本地原件直读方案.md` 第五节），所以应用不再为用户文件保留任何副本。
     *
     * Takes the format's declared name rather than a fixed list of kinds: the
     * main process asks the backend for that format's extensions and size
     * ceiling, so the set of pickable formats is whatever the backend can
     * parse. A closed union here would silently exclude every plugin format.
     */
    chooseSource(format: string): Promise<LinkedSource | null>;
  };
  sources: SourcesApi;
  batches: BatchesApi;
  corpus: CorpusApi;
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
  RenameRepositoryInputSchema,
  RepositoryRenameResultSchema,
  RemovedRepositorySchema,
  DocumentSummarySchema,
  DocumentDetailSchema,
  RenameDocumentInputSchema,
  DocumentRenameResultSchema,
  DocumentRemovalResultSchema,
  RemovedDocumentSchema,
  RemovedDocumentRestoreSchema,
  DocumentOriginalChunkSchema,
  SourcePreviewSchema,
  ImportJobSchema,
  SessionSummarySchema,
  MessageSchema,
  EventEnvelopeSchema,
  BackendEventEnvelopeSchema,
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
