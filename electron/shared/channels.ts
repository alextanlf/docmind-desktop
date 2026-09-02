export const IPC_CHANNELS = {
  settingsGet: "settings:get",
  settingsSaveModel: "settings:saveModel",
  settingsTestModel: "settings:testModel",
  settingsClearDiagnostics: "settings:clearDiagnostics",
  embeddingStatus: "embedding:status",
  embeddingPrepare: "embedding:prepare",
  yuqueStatus: "yuque:status",
  yuqueLogin: "yuque:login",
  repositoriesList: "repositories:list",
  repositoriesCreate: "repositories:create",
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
  dialogsChooseSource: "dialogs:chooseSource",
  sourcesStageDirectory: "sources:stageDirectory",
  shellOpenExternal: "shell:openExternal",
  streamCancel: "stream:cancel",
} as const;

export const streamEventChannel = (requestId: string) => `stream:event:${requestId}`;
export const CHANNELS = IPC_CHANNELS;
export const STREAM_EVENT_PREFIX = "stream:event:";

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
