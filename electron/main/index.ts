import { app } from "electron";
import { BackendManager, BackendStartError } from "./backend-manager";
import { BackendProxy } from "./backend-proxy";
import { registerIpcHandlers } from "./ipc-handlers";
import { StagedFileService } from "./staged-files";
import {
  attachRendererLoadDiagnostics,
  createWindow,
  showAfterDidFinishLoad,
} from "./window-manager";
import { logger } from "./logger";
import { isE2ERuntime, readE2EDialogPath } from "./e2e-runtime";
let backend: BackendManager;
let proxy: BackendProxy;
let mainWindow: any = null;
let rendererOrigin: string | null = null;
const PACKAGED_BACKEND_COMMAND =
  "backend/.venv/bin/python";
const PACKAGED_BACKEND_CWD = "backend";
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

function stagedFilesForRuntime(dataDir: string): StagedFileService {
  return new StagedFileService({
    dataDir,
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
app.whenReady().then(async () => {
  try {
    if (app.isPackaged && !packagedArgs.valid) throw new BackendStartError();
    backend = new BackendManager({
      dataDir: app.getPath("userData"),
      repoDir: process.cwd(),
      packaged: app.isPackaged,
      backendCommand:
        process.env.DOCMIND_BACKEND_COMMAND ??
        (app.isPackaged ? PACKAGED_BACKEND_COMMAND : undefined),
      backendArgs: packagedArgs.args ?? (app.isPackaged ? ["-m", "app"] : undefined),
      backendCwd:
        process.env.DOCMIND_BACKEND_CWD ?? (app.isPackaged ? PACKAGED_BACKEND_CWD : undefined),
    });
    await backend.start();
    proxy = new BackendProxy({
      request: (path, init) => backend.request(path, init),
    });
    registerIpcHandlers({
      proxy,
      stagedFiles: stagedFilesForRuntime(app.getPath("userData")),
      app,
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
