import { z } from "zod";

const id = z.string().uuid();
const text = (max: number) => z.string().trim().min(1).max(max);
const bounded = (max: number) => z.string().min(1).max(max);
const nullableText = (max: number) => z.string().max(max).nullable();
const timestamp = z.string().min(1);

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

export const ModelSettingsInputSchema = z.object({
  preset: z.enum(["deepseek", "qwen", "openai", "custom"]),
  baseUrl: z.string().max(500),
  model: z.string().max(200),
  timeoutSeconds: z.number().positive().max(300),
  apiKey: z.string().max(2_000).nullable().optional(),
});
export const ModelSettingsViewSchema = z.object({
  preset: z.string(),
  baseUrl: z.string(),
  model: z.string(),
  timeoutSeconds: z.number(),
});
export const SettingsViewSchema = z.object({
  model: ModelSettingsViewSchema,
  hasApiKey: z.boolean(),
  dataPath: z.string(),
  screenshotCount: z.number().int().nonnegative(),
  webSearch: z
    .object({
      provider: z.literal("tavily"),
      mode: z.enum(["off", "ask", "auto"]),
      maxResults: z.number().int().min(1).max(10),
      hasApiKey: z.boolean(),
    })
    .default({ provider: "tavily", mode: "ask", maxResults: 5, hasApiKey: false }),
  runtime: z
    .object({
      ollama: z.object({
        baseUrl: z.string(),
        model: z.string().max(200),
        timeoutSeconds: z.number().positive().max(600),
      }),
      routing: z.object({ mode: z.enum(["local_only", "cloud_only", "automatic"]) }),
    })
    .optional(),
});
export const RuntimeSettingsInputSchema = z.object({
  ollama: z.object({
    baseUrl: z.string(),
    model: z.string().max(200),
    timeoutSeconds: z.number().positive().max(600),
  }),
  routing: z.object({ mode: z.enum(["local_only", "cloud_only", "automatic"]) }),
});
export const OllamaStatusSchema = z.object({
  available: z.boolean(),
  baseUrl: z.string(),
  version: z.string().nullable().optional(),
  selectedModel: z.string(),
  selectedModelInstalled: z.boolean(),
  checkedAt: timestamp,
  message: z.string(),
});
export const OllamaModelSchema = z.object({
  name: z.string(),
  digest: z.string().nullable().optional(),
  sizeBytes: z.number().nullable().optional(),
  modifiedAt: timestamp.nullable().optional(),
  family: z.string().nullable().optional(),
});
export const OllamaModelsSchema = z.object({
  available: z.boolean(),
  models: OllamaModelSchema.array(),
  checkedAt: timestamp,
  message: z.string(),
});
export const OllamaPullInputSchema = z.object({
  modelName: z
    .string()
    .trim()
    .min(1)
    .max(200)
    .regex(/^[^\r\n]+$/),
});
export const OllamaPullSchema = z.object({
  id,
  modelName: z.string(),
  baseUrl: z.string(),
  state: z.enum(["queued", "running", "completed", "failed", "cancelled"]),
  progress: z.number().int().min(0).max(100),
  status: z.string().nullable().optional(),
  totalBytes: z.number().nullable().optional(),
  completedBytes: z.number().nullable().optional(),
  errorCode: z.string().nullable().optional(),
  errorMessage: z.string().nullable().optional(),
  retryable: z.boolean(),
  cancelRequested: z.boolean(),
  lastEventSequence: z.number().int().nonnegative(),
  createdAt: timestamp,
  startedAt: timestamp.nullable().optional(),
  completedAt: timestamp.nullable().optional(),
  updatedAt: timestamp,
});
export type OllamaStatusView = z.infer<typeof OllamaStatusSchema>;
export type OllamaModelView = z.infer<typeof OllamaModelSchema>;
export type OllamaModelsView = z.infer<typeof OllamaModelsSchema>;
export const WebSearchSettingsInputSchema = z.object({
  mode: z.enum(["off", "ask", "auto"]),
  maxResults: z.number().int().min(1).max(10),
  apiKey: z.string().max(2_000).nullable().optional(),
});
export const ModelConnectionResultSchema = z.object({
  connected: z.boolean(),
  latencyMs: z.number().int().nonnegative(),
});

export const ModelStatusSchema = z.object({
  state: z.enum(["unavailable", "downloading", "ready", "error"]),
  modelName: z.string(),
  dimension: z.number().int().positive().nullable().optional(),
  message: z.string(),
  progress: z.number().int().min(0).max(100).nullable().optional(),
});
export const YuqueStatusSchema = z.object({
  loggedIn: z.boolean(),
  accountLabel: z.string().nullable().optional(),
  requiresLogin: z.boolean(),
});

export const RepositorySchema = z.object({
  id,
  yuqueId: z.string().nullable().optional(),
  name: text(120),
  description: nullableText(2_000),
  yuqueUrl: z.string().max(4_000).nullable().optional(),
  documentCount: z.number().int().nonnegative(),
  indexedDocumentCount: z.number().int().nonnegative(),
  syncStatus: z.string(),
  createdAt: timestamp,
  updatedAt: timestamp,
});
export const CreateRepositoryInputSchema = z.object({ name: text(120) });

export const DocumentInputSchema = z.object({
  title: text(240),
  content: bounded(2_000_000),
});
export const DocumentSummarySchema = z.object({
  id,
  repositoryId: id,
  yuqueId: z.string().nullable().optional(),
  title: text(240),
  yuqueUrl: z.string().max(4_000).nullable().optional(),
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
export const DocumentDetailSchema = DocumentSummarySchema.extend({
  content: z.string().max(2_000_000),
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
  sourceKind: z.enum(["staged_directory", "web", "yuque_repository", "search_results"]),
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
export const CreateBatchInputSchema = z.object({
  kind: z.literal("staged_directory"),
  sourceId: id,
  repositoryId: id,
});
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
  z.object({ target: z.literal("yuque"), repositoryId: id }),
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
  storageTarget: z.enum(["local", "yuque"]).nullable(),
  localPath: relativePath.nullable(),
  documentId: id.nullable(),
  yuqueUrl: z.string().url().max(4_000).nullable(),
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
export type ModelConnectionResult = z.infer<typeof ModelConnectionResultSchema>;
export type ModelStatus = z.infer<typeof ModelStatusSchema>;
export type YuqueStatus = z.infer<typeof YuqueStatusSchema>;
export type Repository = z.infer<typeof RepositorySchema>;
export type CreateRepositoryInput = z.infer<typeof CreateRepositoryInputSchema>;
export type SyncOutcome = z.infer<typeof SyncOutcomeSchema>;
export type SyncStatus = z.infer<typeof SyncStatusSchema>;
export type ConflictView = z.infer<typeof ConflictViewSchema>;
export type ConflictResolution = z.infer<typeof ConflictResolutionSchema>;
export type DocumentVersion = z.infer<typeof DocumentVersionSchema>;
export type DocumentInput = z.infer<typeof DocumentInputSchema>;
export type DocumentSummary = z.infer<typeof DocumentSummarySchema>;
export type DocumentDetail = z.infer<typeof DocumentDetailSchema>;
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
  ollama: {
    status(): Promise<z.infer<typeof OllamaStatusSchema>>;
    models(): Promise<z.infer<typeof OllamaModelsSchema>>;
    pull(input: z.infer<typeof OllamaPullInputSchema>): Promise<z.infer<typeof OllamaPullSchema>>;
    getPull(id: string): Promise<z.infer<typeof OllamaPullSchema>>;
    cancelPull(id: string): Promise<z.infer<typeof OllamaPullSchema>>;
    retryPull(id: string): Promise<z.infer<typeof OllamaPullSchema>>;
    subscribePull(
      id: string,
      afterSequence: number,
      onEvent: (event: EventEnvelope) => void,
    ): StreamSubscription;
  };
  settings: {
    get(): Promise<SettingsView>;
    saveModel(input: ModelSettingsInput): Promise<SettingsView>;
    testModel(): Promise<ModelConnectionResult>;
    clearDiagnostics(): Promise<void>;
    saveWebSearch(input: WebSearchSettingsInput): Promise<SettingsView>;
    saveRuntime(input: z.infer<typeof RuntimeSettingsInputSchema>): Promise<SettingsView>;
  };
  embedding: {
    status(): Promise<ModelStatus>;
    prepare(): Promise<ModelStatus>;
  };
  yuque: { status(): Promise<YuqueStatus>; login(): Promise<YuqueStatus> };
  repositories: {
    list(): Promise<Repository[]>;
    create(input: CreateRepositoryInput): Promise<Repository>;
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
  documents: {
    list(repositoryId: string): Promise<DocumentSummary[]>;
    read(documentId: string): Promise<DocumentDetail>;
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
    chooseSource(kind: "pdf" | "markdown"): Promise<StagedSource | null>;
  };
  sources: SourcesApi;
  batches: BatchesApi;
  shell: { openExternal(url: string): Promise<void> };
}

export const schemas = {
  SettingsViewSchema,
  ModelConnectionResultSchema,
  ModelStatusSchema,
  YuqueStatusSchema,
  RepositorySchema,
  DocumentSummarySchema,
  DocumentDetailSchema,
  SourcePreviewSchema,
  ImportJobSchema,
  SessionSummarySchema,
  MessageSchema,
  EventEnvelopeSchema,
  BackendEventEnvelopeSchema,
  StagedSourceSchema,
  StagedCollectionSchema,
  BatchImportSchema,
  BatchItemSchema,
  BatchProgressPayloadSchema,
  BatchItemPageSchema,
  SessionMemorySummarySchema,
  DistillationViewSchema,
  MemoryItemPageSchema,
};
