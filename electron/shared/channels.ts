export const IPC_CHANNELS = {
  localModelStatus: "localModel:status",
  localModelModels: "localModel:models",
  settingsGet: "settings:get",
  settingsSaveModel: "settings:saveModel",
  settingsTestModel: "settings:testModel",
  settingsListModels: "settings:listModels",
  settingsSkipModelSetup: "settings:skipModelSetup",
  settingsClearDiagnostics: "settings:clearDiagnostics",
  settingsSaveWebSearch: "settings:saveWebSearch",
  settingsSaveRuntime: "settings:saveRuntime",
  embeddingStatus: "embedding:status",
  embeddingPrepare: "embedding:prepare",
  remoteStatus: "remote:status",
  remoteLogin: "remote:login",
  remoteInstallBrowser: "remote:installBrowser",
  remoteListProviders: "remote:listProviders",
  remoteListCredentials: "remote:listCredentials",
  remoteSaveCredential: "remote:saveCredential",
  remoteTestCredential: "remote:testCredential",
  remoteDeleteCredential: "remote:deleteCredential",
  pluginsList: "plugins:list",
  pluginsDiagnostics: "plugins:diagnostics",
  pluginsDirectory: "plugins:directory",
  repositoriesList: "repositories:list",
  repositoriesCreate: "repositories:create",
  repositoriesUpdate: "repositories:update",
  syncGet: "sync:get",
  syncTrigger: "sync:trigger",
  conflictsList: "conflicts:list",
  conflictsResolve: "conflicts:resolve",
  versionsList: "versions:list",
  graphNodes: "graph:nodes",
  graphAdjacency: "graph:adjacency",
  documentsList: "documents:list",
  documentsRead: "documents:read",
  documentsCreate: "documents:create",
  documentsUpdate: "documents:update",
  documentsDelete: "documents:delete",
  importsInspect: "imports:inspect",
  importsCreate: "imports:create",
  importsGet: "imports:get",
  importsRetry: "imports:retry",
  importsCancel: "imports:cancel",
  importsSubscribe: "imports:subscribe",
  chatListSessions: "chat:listSessions",
  chatCreateSession: "chat:createSession",
  chatListMessages: "chat:listMessages",
  chatStream: "chat:stream",
  chatSearchStream: "chat:searchStream",
  webSearchGetRun: "webSearch:getRun",
  webSearchCreateImportBatch: "webSearch:createImportBatch",
  memoryEndSession: "memory:endSession",
  memoryDeleteSession: "memory:deleteSession",
  memoryGetSummary: "memory:getSummary",
  memoryRegenerateSummary: "memory:regenerateSummary",
  memoryDeleteSummary: "memory:deleteSummary",
  memoryCreateDistillation: "memory:createDistillation",
  memoryGetDistillation: "memory:getDistillation",
  memoryUpdateDistillation: "memory:updateDistillation",
  memoryRegenerateDistillation: "memory:regenerateDistillation",
  memorySaveDistillation: "memory:saveDistillation",
  memoryDeleteDistillation: "memory:deleteDistillation",
  memoryList: "memory:list",
  memorySubscribeDistillation: "memory:subscribeDistillation",
  dialogsChooseSource: "dialogs:chooseSource",
  sourcesStageDirectory: "sources:stageDirectory",
  sourcesListFormats: "sources:listFormats",
  batchesCreate: "batches:create",
  batchesGet: "batches:get",
  batchesList: "batches:list",
  batchesListItems: "batches:listItems",
  batchesConfirm: "batches:confirm",
  batchesCancel: "batches:cancel",
  batchesContinue: "batches:continue",
  batchesRetry: "batches:retry",
  batchesSubscribe: "batches:subscribe",
  shellOpenExternal: "shell:openExternal",
  appRestart: "app:restart",
  streamCancel: "stream:cancel",
} as const;

const STREAM_EVENT_PREFIX = "stream:event:";

export const streamEventChannel = (requestId: string) =>
  `${STREAM_EVENT_PREFIX}${requestId}`;

/**
 * 走「推送」语义的通道集合：preload 侧一律用 `ipcRenderer.send`，
 * 主进程侧必须用 `ipcMain.on` 注册。
 *
 * 🔴 为什么必须集中在这里：两边的注册方式必须严格配对 ——
 * `ipcMain.handle` 注册的处理器**永远不会被** `ipcRenderer.send` 触发
 * （send 只投递 `ipcMain.on` 的监听器），反之亦然。
 * 这份名单此前是 `ipc-handlers.ts` 里一个手写 if 白名单，新增通道时
 * 极易漏掉：批量导入的 `batchesSubscribe` 就曾漏掉，
 * 导致批量导入进度**一个事件都收不到**，
 * 而前端 `BatchProgress` 没有轮询兜底，进度条直接卡死在初始快照。
 *
 * 判据可验证：`electron/tests/main/ipc-handlers.test.ts` 断言
 * 「凡在 preload 里 send 的通道，都在本集合内」。
 */
export const PUSH_CHANNELS: ReadonlySet<string> = new Set<string>([
  IPC_CHANNELS.importsSubscribe,
  IPC_CHANNELS.batchesSubscribe,
  IPC_CHANNELS.chatStream,
  IPC_CHANNELS.chatSearchStream,
  IPC_CHANNELS.memorySubscribeDistillation,
  IPC_CHANNELS.streamCancel,
]);

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
