import { app, shell } from "electron";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { BackendManager, BackendStartError } from "./backend-manager";
import { BackendProxy } from "./backend-proxy";
import { registerIpcHandlers } from "./ipc-handlers";
import { StagedFileService } from "./staged-files";
import {
  attachRendererLoadDiagnostics,
  createWindow,
  resolveIconPath,
  showAfterDidFinishLoad,
} from "./window-manager";
import { logger } from "./logger";
import { isE2ERuntime, readE2EDialogPath } from "./e2e-runtime";
let backend: BackendManager;
let proxy: BackendProxy;
let mainWindow: any = null;
let rendererOrigin: string | null = null;
const e2eRuntime = isE2ERuntime(process.env, app.isPackaged);
let waitingForBackendExit = false;

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (app.isReady()) openMainWindow();
  });
}

if (e2eRuntime && process.env.DOCMIND_E2E_DATA_DIR) {
  app.setPath("userData", process.env.DOCMIND_E2E_DATA_DIR);
}

function stagedFilesForRuntime(dataDir: string, sessionToken: string): StagedFileService {
  return new StagedFileService({
    dataDir,
    // 与后端共享的会话密钥：链接型导入的路径签名用它。没有它，"用户选过的路径"就
    // 没法向后端证明 —— 而那是渲染层读不到任意文件的唯一依据。
    sessionToken,
    ...(e2eRuntime
      ? {
          showOpenDialog: async () => {
            const path = await readE2EDialogPath(dataDir);
            return path
              ? { canceled: false, filePaths: [path] }
              : { canceled: true, filePaths: [] };
          },
        }
      : {}),
  });
}

function openMainWindow() {
  if (!rendererOrigin) return;
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    return;
  }
  const win = createWindow(rendererOrigin);
  attachRendererLoadDiagnostics(win, (error) => logger.error(error));
  showAfterDidFinishLoad(win);
  mainWindow = win;
  win.on("closed", () => {
    mainWindow = null;
  });
  void win.loadURL(rendererOrigin);
}

function parsePackagedArgs(value: string | undefined) {
  if (value === undefined) return { valid: true, args: undefined };
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed) || parsed.some((argument) => typeof argument !== "string"))
      return { valid: false, args: undefined };
    return { valid: true, args: parsed as string[] };
  } catch {
    return { valid: false, args: undefined };
  }
}
const packagedArgs = parsePackagedArgs(process.env.DOCMIND_BACKEND_ARGS);
/** 开发态沿用 Electron 应用包时 Dock 会显示 Electron 图标，这里换成 DocMind 自己的图标。 */
function applyDevelopmentDockIcon() {
  if (app.isPackaged) return;
  const iconPath = resolveIconPath();
  if (iconPath) app.dock?.setIcon(iconPath);
}

/** 随应用分发的内置模型目录（打包时经 extraResources 放入 Resources/models）。
 *  后端通过 DOCMIND_BUNDLED_MODELS_DIR 优先从这里解析 embedding 模型，
 *  用户数据目录中显式准备的模型仅作兜底。 */
function resolveBundledModelsDir(): string | undefined {
  if (!app.isPackaged) return undefined;
  const dir = join(process.resourcesPath, "models");
  return existsSync(dir) ? dir : undefined;
}

app.whenReady().then(async () => {
  try {
    applyDevelopmentDockIcon();
    if (app.isPackaged && !packagedArgs.valid) throw new BackendStartError();
    backend = new BackendManager({
      dataDir: app.getPath("userData"),
      repoDir: process.cwd(),
      packaged: app.isPackaged,
      backendCommand: process.env.DOCMIND_BACKEND_COMMAND,
      backendArgs: packagedArgs.args,
      backendCwd: process.env.DOCMIND_BACKEND_CWD,
      bundledModelsDir: resolveBundledModelsDir(),
    });
    const connection = await backend.start();
    proxy = new BackendProxy({
      request: (path, init) => backend.request(path, init),
    });
    registerIpcHandlers({
      proxy,
      stagedFiles: stagedFilesForRuntime(app.getPath("userData"), connection.token),
      app,
      // 主进程要自己验后端发来的路径签名（"用系统程序打开原件"那一步），所以要有一份密钥。
      sessionToken: connection.token,
      shell,
    });
    rendererOrigin =
      process.env.ELECTRON_RENDERER_URL ?? `file://${__dirname}/../renderer/index.html`;
    openMainWindow();
  } catch (error) {
    logger.error(error);
    app.quit();
  }
});
app.on("before-quit", (event) => {
  if (!backend) return;
  if (waitingForBackendExit) return;
  event.preventDefault();
  waitingForBackendExit = true;
  proxy?.cleanup();
  void backend
    ?.stop()
    .then((exited) => {
      if (!exited) logger.error(new Error("Backend did not exit after shutdown signals"));
    })
    .catch((error) => logger.error(error))
    .finally(() => app.quit());
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
app.on("activate", () => {
  openMainWindow();
});
