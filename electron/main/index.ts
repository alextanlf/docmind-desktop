import { app } from "electron";
import { BackendManager } from "./backend-manager";
import { createWindow, showAfterDidFinishLoad } from "./window-manager";
import { logger } from "./logger";
let backend: BackendManager;
let packagedArgs: unknown;
try {
  packagedArgs = process.env.DOCMIND_BACKEND_ARGS
    ? JSON.parse(process.env.DOCMIND_BACKEND_ARGS)
    : undefined;
} catch {
  packagedArgs = undefined;
}
app.whenReady().then(async () => {
  backend = new BackendManager({
    dataDir: app.getPath("userData"),
    repoDir: process.cwd(),
    packaged: app.isPackaged,
    backendCommand: process.env.DOCMIND_BACKEND_COMMAND,
    backendArgs: Array.isArray(packagedArgs) ? packagedArgs : undefined,
    backendCwd: process.env.DOCMIND_BACKEND_CWD,
  });
  try {
    await backend.start();
    const origin =
      process.env.ELECTRON_RENDERER_URL ??
      `file://${__dirname}/../renderer/index.html`;
    const win = createWindow(origin);
    showAfterDidFinishLoad(win);
    await win.loadURL(origin);
  } catch (error) {
    logger.error(error);
    app.quit();
  }
});
app.on("before-quit", () => {
  void backend?.stop();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
