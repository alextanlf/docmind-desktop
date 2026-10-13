export const IPC_CHANNELS = {
  localModelStatus: "localModel:status",
  localModelModels: "localModel:models",
  settingsGet: "settings:get",
  settingsSaveModel: "settings:saveModel",
  settingsDeleteModel: "settings:deleteModel",
  settingsActivateModel: "settings:activateModel",
  settingsTestModel: "settings:testModel",
  settingsListModels: "settings:listModels",
  settingsSkipModelSetup: "settings:skipModelSetup",
  settingsClearDiagnostics: "settings:clearDiagnostics",
  settingsSetFigureOcr: "settings:setFigureOcr",
  settingsSetFigureDescription: "settings:setFigureDescription",
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
  pluginsSetEnabled: "plugins:setEnabled",
  pluginsUninstall: "plugins:uninstall",
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
  documentsReadOriginalChunk: "documents:readOriginalChunk",
  documentsReadAsset: "documents:readAsset",
  documentsCreate: "documents:create",
  documentsUpdate: "documents:update",
  documentsDelete: "documents:delete",
  documentsRelinkOriginal: "documents:relinkOriginal",
  documentsReconvert: "documents:reconvert",
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
  /**
   * 后台扫描一个目录，边扫边推进度。
   *
   * 是**推送型**通道（见 PUSH_CHANNELS）：它立刻返回 scanId，结果与进度走
   * `streamEventChannel(scanId)`；`streamCancel` 用来取消。
   *
   * 从前这里是 `sources:stageDirectory`（一问一答、扫完才返回）。改成推送是因为
   * 扫描要把每个文件完整读一遍算摘要 —— 几百个文件就是几分钟，界面既看不到进度
   * 也没法取消。
   */
  /**
   * 用系统程序打开这篇文档的原件（Word、Preview……）。
   *
   * 渲染层**只发 documentId**：真正的路径由主进程向后端要、验签之后才交给系统程序。
   * 若让渲染层把路径传下来，"渲染层不能指名任意文件"这条性质当场就没了 —— 而这个通道
   * 的目的恰恰是去动用户的文件。
   */
  documentsOpenOriginal: "documents:openOriginal",
  sourcesScanDirectory: "sources:scanDirectory",
  sourcesListFormats: "sources:listFormats",
  batchesCreate: "batches:create",
  batchesGet: "batches:get",
  batchesList: "batches:list",
  batchesListItems: "batches:listItems",
  batchesDiagnostics: "batches:diagnostics",
  batchesConfirm: "batches:confirm",
  batchesCancel: "batches:cancel",
  batchesContinue: "batches:continue",
  batchesRetry: "batches:retry",
  batchesSubscribe: "batches:subscribe",
  corpusCandidates: "corpus:candidates",
  corpusTopics: "corpus:topics",
  corpusSources: "corpus:sources",
  corpusSaveSources: "corpus:saveSources",
  corpusRemoveSource: "corpus:removeSource",
  corpusCrawlSource: "corpus:crawlSource",
  corpusCancelCrawl: "corpus:cancelCrawl",
  shellOpenExternal: "shell:openExternal",
  appRestart: "app:restart",
  streamCancel: "stream:cancel",
} as const;

const STREAM_EVENT_PREFIX = "stream:event:";

export const streamEventChannel = (requestId: string) => `${STREAM_EVENT_PREFIX}${requestId}`;

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
  IPC_CHANNELS.sourcesScanDirectory,
  IPC_CHANNELS.importsSubscribe,
  IPC_CHANNELS.batchesSubscribe,
  IPC_CHANNELS.chatStream,
  IPC_CHANNELS.chatSearchStream,
  IPC_CHANNELS.memorySubscribeDistillation,
  IPC_CHANNELS.streamCancel,
]);

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
