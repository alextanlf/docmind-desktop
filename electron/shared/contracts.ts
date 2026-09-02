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
  createdAt: timestamp,
  updatedAt: timestamp,
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
      return { ...raw, sourceUrl: null };
    return raw;
  },
  z.object({
    sourceId: z.string().max(255),
    chunkId: z.string().max(255),
    documentId: id,
    title: text(240),
    sectionPath: z.string().max(1_000).nullable().optional(),
    pageNumber: z.number().int().positive().nullable().optional(),
    excerpt: z.string().max(5_000),
    sourceUrl: z.string().max(4_000).nullable().optional(),
  }),
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
  totalBytes: z.number().int().nonnegative().max(2 * 1024 ** 3),
});
export const ChatStreamInputSchema = z.object({
  requestId: id,
  sessionId: id,
  message: bounded(20_000),
  repositoryIds: z.array(id).min(1).max(100),
});

export type ErrorBody = z.infer<typeof ErrorBodySchema>;
export type ErrorEnvelope = z.infer<typeof ErrorEnvelopeSchema>;
export type ModelSettingsInput = z.infer<typeof ModelSettingsInputSchema>;
export type SettingsView = z.infer<typeof SettingsViewSchema>;
export type ModelConnectionResult = z.infer<typeof ModelConnectionResultSchema>;
export type ModelStatus = z.infer<typeof ModelStatusSchema>;
export type YuqueStatus = z.infer<typeof YuqueStatusSchema>;
export type Repository = z.infer<typeof RepositorySchema>;
export type CreateRepositoryInput = z.infer<typeof CreateRepositoryInputSchema>;
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
export type ChatStreamInput = z.infer<typeof ChatStreamInputSchema>;

export interface StreamSubscription {
  requestId: string;
  cancel(): void;
  detach(): void;
}

export interface SourcesApi {
  stageDirectory(): Promise<StagedCollection | null>;
}

export interface DocMindApi {
  settings: {
    get(): Promise<SettingsView>;
    saveModel(input: ModelSettingsInput): Promise<SettingsView>;
    testModel(): Promise<ModelConnectionResult>;
    clearDiagnostics(): Promise<void>;
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
  };
  dialogs: {
    chooseSource(kind: "pdf" | "markdown"): Promise<StagedSource | null>;
  };
  sources: SourcesApi;
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
};
